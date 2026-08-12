"""
风险评分卡（纯函数，无 LLM）

设计依据见 docs/DESIGN_CORE_MECHANISMS.md 第二部分。

## 为什么必须是规则而非 LLM 打分

业务上：信贷评审会要问"为什么是高风险"，答案必须是
"资产负债率 89.1% 超过阈值 85%，且存在 2 笔被执行记录"，而不是"模型认为"。

技术上：规则可单测、可复现、零方差、零成本，且能作为评测基准。
LLM 打分不可复现，评测就无从谈起——v0.4 实测的 LLM 判定方差
（同一输入三次不同结论）已经证明了这一点。

**LLM 负责把非结构化信息转成结构化字段，规则负责判断。** 职责分离。

## 最关键的一条：完整度闸门

若不加约束会出现灾难性行为：一家企业因司法数据源故障，
涉诉/被执行/失信三项全部 unverified → 司法维度无扣分 → 综合分很低
→ 系统输出"低风险，建议授信"。

**查不到 ≠ 没问题。** 闸门在综合评分之后强制施加，不可被评分覆盖。
"""
from typing import Any, Dict, List, Optional, Tuple

# —— 风险等级（有序，便于取"至少为 X"）——
LEVELS = ["低风险", "中风险", "高风险", "拒绝"]
INSUFFICIENT = "数据不足，无法评级"

# 维度权重。合计 1.0
WEIGHTS = {
    "financial": 0.30,
    "judicial": 0.30,
    "relation": 0.20,
    "operation": 0.10,
    "opinion": 0.10,
}

# 完整度闸门阈值
MIN_OVERALL_RATE = 0.60      # 总体核实率低于此值 → 不予评级
MIN_CATEGORY_RATE = 0.50     # 维度核实率低于此值 → 该维度不参与加权

# 风险分**从结构化档案取值**计算的核查项。
#
# 这些项的 `_ok()` 只决定"要不要评"，具体扣多少分完全由 `company` 里的
# 结构化数据决定。因此结构化适配器若只把清单改成 verified、数据没进 company，
# 评分卡会照常运行并得出「未发现 XX」——一条负面证据被翻译成正面结论（BC-31）。
#
# `verification.build_scoring_view()` 用这个集合判定哪些证据必须能合并进档案；
# 合并不了就不予评级。**新增会读档案的规则时必须同步登记在此**，
# 否则那条规则会重新打开同一个洞。
PROFILE_BACKED_FIELDS = frozenset({
    "debt_ratio", "net_profit", "revenue", "cash_flow",
    "litigation", "enforcement", "dishonesty",
    "guarantee", "guarantee_circle",
    "operating_status", "bidding_record",
    "negative_news", "regulatory_penalty",
})


def _level_at_least(current: str, floor: str) -> str:
    """取更严的等级。INSUFFICIENT 不参与比较，由调用方单独处理"""
    if current == INSUFFICIENT:
        return current
    ci = LEVELS.index(current) if current in LEVELS else 0
    fi = LEVELS.index(floor)
    return LEVELS[max(ci, fi)]


def _score_to_level(score: float) -> str:
    if score <= 25:
        return "低风险"
    if score <= 50:
        return "中风险"
    if score <= 75:
        return "高风险"
    return "拒绝"


# ---------------------------------------------------------------- 指标打分
# 每个指标返回 (分值 0-100, 说明)。分越高风险越大。

def _score_debt_ratio(v: float) -> Tuple[float, str]:
    if v <= 0.50:
        return 0.0, f"资产负债率 {v:.1%}，处于安全区间（≤50%）"
    if v <= 0.70:
        return 30.0, f"资产负债率 {v:.1%}，略高（50%-70%）"
    if v <= 0.85:
        return 60.0, f"资产负债率 {v:.1%}，偏高（70%-85%）"
    return 100.0, f"资产负债率 {v:.1%}，超过 85% 警戒线"


def _score_net_margin(net: float, rev: float) -> Tuple[float, str]:
    if not rev:
        return 50.0, "营收为零或缺失，无法计算净利率"
    m = net / rev
    if m >= 0.10:
        return 0.0, f"净利率 {m:.1%}，盈利能力良好"
    if m >= 0:
        return 30.0, f"净利率 {m:.1%}，盈利能力偏弱"
    if m >= -0.10:
        return 70.0, f"净利率 {m:.1%}，已出现亏损"
    return 100.0, f"净利率 {m:.1%}，严重亏损"


def _score_revenue_trend(series: List[float]) -> Tuple[float, str]:
    if len(series) < 2:
        return 30.0, "营收期数不足，无法判断趋势"
    first, last = series[0], series[-1]
    if not first:
        return 30.0, "基期营收为零，无法计算变动"
    chg = (last - first) / abs(first)
    if chg > 0.05:
        return 0.0, f"营收较基期增长 {chg:.1%}"
    if chg >= -0.05:
        return 30.0, f"营收基本持平（{chg:+.1%}）"
    if chg > -0.20:
        return 60.0, f"营收下滑 {abs(chg):.1%}"
    return 100.0, f"营收大幅下滑 {abs(chg):.1%}"


def _score_cash_flow(series: List[float]) -> Tuple[float, str]:
    if not series:
        return 50.0, "缺少经营性现金流数据"
    neg = [x for x in series if x < 0]
    if not neg:
        return 0.0, "经营性现金流各期均为正"
    if len(neg) >= 2:
        return 100.0, f"经营性现金流连续 {len(neg)} 期为负"
    return 50.0, "经营性现金流出现单期为负"


def _score_judicial(records: List[Dict], revenue: Optional[float]) -> List[Tuple[float, str]]:
    out = []
    dishonest = [r for r in records if r.get("type") == "失信"]
    enforced = [r for r in records if r.get("type") == "被执行"]
    lawsuits = [r for r in records if r.get("type") == "涉诉"]

    if dishonest:
        out.append((100.0, f"存在 {len(dishonest)} 条失信被执行人记录（一票否决类指标）"))
    else:
        out.append((0.0, "未发现失信被执行人记录"))

    if enforced:
        amt = sum(float(r.get("amount") or 0) for r in enforced)
        out.append((100.0 if len(enforced) >= 2 else 70.0,
                    f"存在 {len(enforced)} 笔被执行记录，合计 {amt:.0f} 万元"))
    else:
        out.append((0.0, "未发现被执行记录"))

    if lawsuits:
        amt = sum(float(r.get("amount") or 0) for r in lawsuits
                  if r.get("role") != "原告")
        if revenue and amt:
            ratio = amt / revenue
            score = 100.0 if ratio > 0.10 else (60.0 if ratio > 0.03 else 30.0)
            out.append((score, f"作为被告涉案金额 {amt:.0f} 万元，占营收 {ratio:.1%}"))
        elif amt:
            out.append((30.0, f"作为被告涉案金额 {amt:.0f} 万元"))
        else:
            out.append((0.0, f"{len(lawsuits)} 笔涉诉均为原告方，不构成负面"))
    else:
        out.append((0.0, "未发现涉诉记录"))
    return out


# ---------------------------------------------------------------- 主入口

def score(
    company: Dict[str, Any],
    field_checks: List[Dict],
    completeness: Dict[str, Any],
) -> Dict[str, Any]:
    """
    计算风险评分与等级。

    只使用 status == "verified" 的字段参与打分——这是完整度闸门的前提：
    未核实的字段不能贡献"没有风险"的信号。
    """
    by_id = {c["field_id"]: c for c in field_checks}

    def _ok(fid: str) -> bool:
        return by_id.get(fid, {}).get("status") == "verified"

    fins = company.get("financials") or []
    revenue_series = [f["revenue"] for f in fins if f.get("revenue") is not None]
    latest = fins[-1] if fins else {}

    dim_scores: Dict[str, float] = {}
    triggered: List[Dict[str, Any]] = []
    skipped: List[str] = []

    def _add_rule(dim: str, s: float, detail: str, fid: str):
        check = by_id.get(fid, {})
        # 初始档案沿用 fact_id；结构化适配器必须直接引用 evidence_store 中的
        # evidence_id。此前这里只读 sources，导致适配器触发的规则 evidence=[]，
        # 报告虽然扣了分，却无法回答“依据是哪条原始证据”（BC-39）。
        evidence = (check.get("evidence_ids") or []
                    if check.get("verification_origin") == "structured_adapter"
                    else check.get("sources") or [])
        triggered.append({"dimension": dim, "score": s, "detail": detail,
                          "field_id": fid, "evidence": list(evidence)})

    # —— 财务 ——
    fin_items = []
    if _ok("debt_ratio") and latest.get("debt_ratio") is not None:
        s, d = _score_debt_ratio(latest["debt_ratio"]); fin_items.append(s); _add_rule("financial", s, d, "debt_ratio")
    if _ok("net_profit") and _ok("revenue") and latest.get("net_profit") is not None:
        s, d = _score_net_margin(latest["net_profit"], latest.get("revenue") or 0); fin_items.append(s); _add_rule("financial", s, d, "net_profit")
    if _ok("revenue") and revenue_series:
        s, d = _score_revenue_trend(revenue_series); fin_items.append(s); _add_rule("financial", s, d, "revenue")
    if _ok("cash_flow"):
        s, d = _score_cash_flow([f["operating_cash_flow"] for f in fins
                                 if f.get("operating_cash_flow") is not None])
        fin_items.append(s); _add_rule("financial", s, d, "cash_flow")
    if fin_items:
        dim_scores["financial"] = sum(fin_items) / len(fin_items)

    # —— 司法 ——
    jud_ok = [f for f in ("litigation", "enforcement", "dishonesty") if _ok(f)]
    if jud_ok:
        items = _score_judicial(company.get("judicial_records") or [],
                                latest.get("revenue"))
        for (s, d), fid in zip(items, ("dishonesty", "enforcement", "litigation")):
            if _ok(fid):
                _add_rule("judicial", s, d, fid)
        vals = [s for (s, _), fid in zip(items, ("dishonesty", "enforcement", "litigation")) if _ok(fid)]
        if vals:
            dim_scores["judicial"] = sum(vals) / len(vals)

    # —— 关联 ——
    rel_items = []
    if _ok("guarantee"):
        g = company.get("guarantee") or []
        amt = sum(float(x.get("amount") or 0) for x in g)
        if not g:
            rel_items.append(0.0); _add_rule("relation", 0.0, "未发现对外担保", "guarantee")
        else:
            s = 70.0 if amt >= 1000 else 40.0
            rel_items.append(s); _add_rule("relation", s, f"对外担保 {len(g)} 笔，合计 {amt:.0f} 万元", "guarantee")
    if _ok("guarantee_circle"):
        # 必须读实际数据。此前这里硬编码 0.0 —— 一旦 guarantee_circle 变成
        # verified（v0.6 图谱推导上线后就会），无论查到什么都会记成
        # "未发现担保圈"，与 BC-31 同形：核实状态被当成了结论本身。
        circle = company.get("guarantee_circle") or []
        if circle:
            s = 100.0 if len(circle) >= 2 else 70.0
            rel_items.append(s)
            _add_rule("relation", s, f"涉入担保圈 {len(circle)} 条互保/连环担保关系",
                      "guarantee_circle")
        else:
            rel_items.append(0.0)
            _add_rule("relation", 0.0, "未发现担保圈涉入情况", "guarantee_circle")
    if rel_items:
        dim_scores["relation"] = sum(rel_items) / len(rel_items)

    # —— 经营 ——
    op_items = []
    if _ok("operating_status"):
        st = (company.get("registration") or {}).get("operating_status", "")
        s = 0.0 if st in ("存续", "在业") else 100.0
        op_items.append(s); _add_rule("operation", s, f"登记状态：{st or '未知'}", "operating_status")
    if _ok("bidding_record"):
        n = len(company.get("bidding_records") or [])
        s = 0.0 if n else 30.0
        op_items.append(s); _add_rule("operation", s, f"中标记录 {n} 条", "bidding_record")
    if op_items:
        dim_scores["operation"] = sum(op_items) / len(op_items)

    # —— 舆情 ——
    op2 = []
    if _ok("negative_news"):
        news = [n for n in (company.get("negative_news") or [])
                if n.get("subject_confirmed") is True]
        sev = [n.get("severity") for n in news]
        s = 100.0 if "严重" in sev else (40.0 if news else 0.0)
        op2.append(s); _add_rule("opinion", s, f"已确认负面舆情 {len(news)} 条", "negative_news")
    if _ok("regulatory_penalty"):
        pen = company.get("regulatory_penalty") or []
        s = 50.0 if pen else 0.0
        op2.append(s); _add_rule("opinion", s, f"监管处罚 {len(pen)} 条", "regulatory_penalty")
    if op2:
        dim_scores["opinion"] = sum(op2) / len(op2)

    # —— 加权：核实率过低的维度不参与 ——
    by_cat = completeness.get("by_category") or {}
    usable = {}
    # 必须遍历「应评估的维度」，不能只遍历已经算出分数的维度。
    # 整个维度都没有 verified 字段时，dim_scores 恰好没有该键；若从
    # dim_scores 出发，最严重的 0% 核实率反而不会进入 skipped，完整度
    # 闸门就会失效（BC-21）。完全不适用于当前主体、因而不在 by_category
    # 出现的维度则不强行施加闸门。
    for dim in WEIGHTS:
        if dim not in by_cat:
            continue
        rate = (by_cat.get(dim) or {}).get("rate", 0.0)
        if rate < MIN_CATEGORY_RATE:
            skipped.append(dim)
            continue
        if dim in dim_scores:
            usable[dim] = dim_scores[dim]

    if usable:
        wsum = sum(WEIGHTS[d] for d in usable)
        composite = sum(usable[d] * WEIGHTS[d] for d in usable) / wsum
    else:
        composite = 0.0

    level = _score_to_level(composite)
    gates: List[str] = []
    requires_review = False

    # ================= 完整度闸门（不可被评分覆盖） =================
    rate = completeness.get("verified_rate", 0.0)

    # 1) 总体核实率过低 → 不予评级
    if rate < MIN_OVERALL_RATE:
        level = INSUFFICIENT
        gates.append(f"总体核实率 {rate:.0%} 低于 {MIN_OVERALL_RATE:.0%}，不具备评级条件")
        requires_review = True

    # 2) 司法维度任一必查项未核实 → 至少中风险（司法是硬约束）
    jud_missing = [f for f in ("litigation", "enforcement", "dishonesty") if not _ok(f)]
    if jud_missing:
        if level != INSUFFICIENT:
            level = _level_at_least(level, "中风险")
        gates.append(f"司法维度必查项未核实（{'、'.join(jud_missing)}），等级下限提升至中风险")
        requires_review = True

    # 3) 某维度核实率过低 → 至少中风险
    for dim in skipped:
        if level != INSUFFICIENT:
            level = _level_at_least(level, "中风险")
        gates.append(f"{dim} 维度核实率不足 {MIN_CATEGORY_RATE:.0%}，不参与加权且等级下限提升")
        requires_review = True

    # 4) 存在冲突必查项 → 上调一级 + 强制人工复核
    conflicts = [c for c in field_checks
                 if c.get("status") == "conflicting" and c.get("required")]
    if conflicts:
        if level in LEVELS:
            level = LEVELS[min(len(LEVELS) - 1, LEVELS.index(level) + 1)]
        gates.append(
            f"存在数据冲突必查项（{'、'.join(c['field_id'] for c in conflicts)}），等级上调一级"
        )
        requires_review = True

    # 5) 一票否决类：失信 / 被执行 → 至少高风险
    jr = company.get("judicial_records") or []
    if _ok("dishonesty") and any(r.get("type") == "失信" for r in jr):
        if level != INSUFFICIENT:
            level = _level_at_least(level, "高风险")
        gates.append("存在失信被执行人记录，等级下限提升至高风险（一票否决类）")
        requires_review = True
    elif _ok("enforcement") and any(r.get("type") == "被执行" for r in jr):
        if level != INSUFFICIENT:
            level = _level_at_least(level, "高风险")
        gates.append("存在被执行记录，等级下限提升至高风险（一票否决类）")
        requires_review = True

    return {
        "composite_score": round(composite, 1),
        "level": level,
        "dimension_scores": {k: round(v, 1) for k, v in dim_scores.items()},
        "dimensions_excluded": skipped,
        "triggered_rules": triggered,
        "gates_applied": gates,
        "requires_human_review": requires_review,
        "completeness": completeness,
        "credit_advice": _advice(level),
    }


def _advice(level: str) -> str:
    return {
        INSUFFICIENT: "信息不足，不得出具授信建议；建议补齐必查项后重新评估",
        "低风险": "可考虑核准授信",
        "中风险": "可考虑授信，建议追加增信措施",
        "高风险": "审慎，建议降额或追加担保；需人工复核",
        "拒绝": "不建议授信",
    }.get(level, "需人工判断")


def apply_provenance_gate(
    result: Dict[str, Any],
    degradations: List[Dict[str, Any]],
    floor: str = "中风险",
) -> Dict[str, Any]:
    """
    来源降级闸门：证据来源不明或取证时间不明时，不得输出最宽松的结论。

    ## 为什么披露不够

    v0.6a 首版把 degradations 写进 `state["errors"]` 就算完事。实测一份
    全部 verified、但全部缺来源标记的旧检查点，得到 20 条 degradation、
    `replay_ok=True`、等级「低风险」、`requires_human_review=False`、
    `gates_applied=[]`——降级信息一条都没进入定级，终局事件里也看不到。
    等于用一行日志换一个可能错误的放款决定（BC-33）。

    与其它闸门一致：**在综合评分之后强制施加，不可被评分覆盖**。

    Args:
        floor: 存在降级时允许达到的最优等级。默认「中风险」。
    """
    if not degradations:
        return result
    if result.get("level") != INSUFFICIENT:
        result["level"] = _level_at_least(result.get("level", "低风险"), floor)
    result["requires_human_review"] = True
    fields = sorted({d.get("field_id") for d in degradations if d.get("field_id")})
    shown = "、".join(fields[:6]) + ("…" if len(fields) > 6 else "")
    result["gates_applied"] = list(result.get("gates_applied") or []) + [
        f"{len(degradations)} 项核实来源或取证时间不明（{shown}），"
        f"等级下限提升至{floor}并强制人工复核；完成来源迁移后方可重新评级"
    ]
    result["provenance_degradations"] = degradations
    result["credit_advice"] = _advice(result["level"])
    return result


def unratable(reason: str, completeness: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    构造「无法评级」结果，用于评分前置条件不满足时。

    **算不出 ≠ 没风险**，与「查不到 ≠ 没问题」是同一条原则。
    评分链路任何一环失效（档案缺失、打分抛异常），都必须落到不可评级 +
    强制人工复核，绝不能因为拿不到扣分项而输出一个低分。

    返回结构与 score() 完全一致，下游无需区分两条路径。
    """
    return {
        "composite_score": 0.0,
        "level": INSUFFICIENT,
        "dimension_scores": {},
        "dimensions_excluded": [],
        "triggered_rules": [],
        "gates_applied": [reason],
        "requires_human_review": True,
        "completeness": completeness or {},
        "credit_advice": _advice(INSUFFICIENT),
    }


# ---------------------------------------------------------------- 渲染

# 报告正文中的评级块锚点。Writer 的代码层兜底据此判断评级是否被模型丢弃，
# 因此这个字符串一旦改动，兜底检测与测试断言必须同步。
RISK_BLOCK_MARKER = "风险评级（规则引擎判定"

# 评级块的结束边界。提示词要求模型原样保留整块，因此模型照抄时会连同它一起复制，
# 使得该块成为**可精确切除的区域**——Writer 据此把模型版本换成规则引擎版本，
# 而不是简单前置一份造成"两个等级"并列。
# 用 HTML 注释：Markdown 渲染后不可见，也不会被 claim_scanner 当作断言句扫到。
RISK_BLOCK_END = "<!-- /risk-assessment -->"


def render_markdown(assessment: Dict[str, Any], max_rules: int = 10) -> str:
    """
    把评分结果渲染成可直接嵌入报告的 Markdown 块。

    ⚠️ 单一渲染入口：Writer 的提示词与代码层兜底用的是同一份文本。
       若两处各写一套，模型看到的评级与最终落进报告的评级就可能不一致。

    渲染时必须同时呈现 level 与 gates_applied——composite_score 会被
    "表现好"的维度稀释（见 tests/test_risk_scorecard.py 中的稀释效应测试），
    单看分数会得出与等级相反的结论。
    """
    if not assessment:
        return ""

    level = assessment.get("level", "未知")
    comp = assessment.get("completeness") or {}
    rate = comp.get("verified_rate")
    rate_txt = (
        f"{comp.get('required_verified', '?')}/{comp.get('required_total', '?')}"
        f"（{rate:.0%}）" if isinstance(rate, (int, float)) else "未统计"
    )

    lines = [
        f"**{RISK_BLOCK_MARKER}，不得由撰写环节改写）**",
        "",
        "| 项目 | 结论 |",
        "|---|---|",
        f"| 风险等级 | **{level}** |",
        f"| 综合评分 | {assessment.get('composite_score', 0.0)} / 100"
        f"（⚠️ 分数会被表现好的维度稀释，不可单独使用，以等级与闸门为准） |",
        f"| 必查项核实率 | {rate_txt} |",
        f"| 人工复核 | {'必须' if assessment.get('requires_human_review') else '非强制'} |",
        f"| 授信建议 | {assessment.get('credit_advice', '')} |",
    ]

    gates = assessment.get("gates_applied") or []
    lines += ["", "**触发的完整度闸门**"]
    if gates:
        lines += [f"- {g}" for g in gates]
    else:
        lines.append("- 无。本次评级完全由维度评分决定，未触发任何闸门")

    rules = sorted(
        assessment.get("triggered_rules") or [],
        key=lambda r: r.get("score", 0), reverse=True
    )
    scored = [r for r in rules if r.get("score", 0) > 0]
    clean = len(rules) - len(scored)
    lines += ["", "**触发的评分规则**"]
    if scored:
        for r in scored[:max_rules]:
            lines.append(f"- [{r.get('dimension')}] {r.get('detail')}（{r.get('field_id')}，{r.get('score')} 分）")
        if len(scored) > max_rules:
            lines.append(f"- （另有 {len(scored) - max_rules} 条扣分规则未列出）")
    else:
        lines.append("- 无扣分规则触发")
    if clean:
        lines.append(f"- 另有 {clean} 项已核实但未构成扣分（属正面结论，不是信息缺失）")

    lines += ["", RISK_BLOCK_END]
    return "\n".join(lines)
