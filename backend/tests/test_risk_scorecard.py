"""
风险评分卡与完整度闸门测试

核心断言：**查不到 ≠ 没问题**。

尽调系统最危险的失效模式是：某数据源故障 → 对应字段全部 unverified
→ 该维度无扣分 → 综合分很低 → 输出"低风险，建议授信"。
闸门必须在综合评分**之后**强制施加，且不可被评分覆盖。

运行：cd backend && python tests/test_risk_scorecard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.risk_scorecard import INSUFFICIENT, LEVELS, score  # noqa: E402


def _checks(status_map):
    """按 field_id -> status 构造清单，未指定者为 verified"""
    cs = build_field_checks()
    for c in cs:
        c["status"] = status_map.get(c["field_id"], "verified")
        if c["status"] == "verified":
            c["value"] = "经查询，无相关记录"
    return cs


def _run(company, status_map=None):
    cs = _checks(status_map or {})
    return score(company, cs, compute_completeness(cs))


_CLEAN = {
    "registration": {"operating_status": "存续"},
    "financials": [{"period": "2025年度", "revenue": 10000.0, "net_profit": 1200.0,
                    "debt_ratio": 0.40, "operating_cash_flow": 900.0}],
    "judicial_records": [], "negative_news": [], "guarantee": [],
    "bidding_records": [{"project": "x", "amount": 100.0, "win_date": "2025-01-01"}],
}


# ---------- ⭐ 核心：查不到 ≠ 没问题 ----------

def test_司法源故障不得判低风险():
    """
    最危险的失效模式：司法三项全未核实，财务健康 → 综合分接近 0。
    没有闸门就会输出"低风险，建议授信"。
    """
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    assert r["composite_score"] < 25, "前提：分数本身很低"
    assert r["level"] != "低风险", "司法维度缺失时绝不能判低风险"
    assert any("司法" in g for g in r["gates_applied"])
    assert r["requires_human_review"] is True


def test_核实率过低时拒绝评级():
    unv = {c["field_id"]: "unverified" for c in build_field_checks()
           if c["field_id"] not in ("registration", "business_scope")}
    r = _run(_CLEAN, unv)
    assert r["level"] == INSUFFICIENT
    assert "不得出具授信建议" in r["credit_advice"] or "补齐" in r["credit_advice"]


def test_未核实字段不得贡献无风险信号():
    """未核实的司法字段不应被当作『无记录』计 0 分"""
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    jud_rules = [x for x in r["triggered_rules"] if x["dimension"] == "judicial"]
    assert not jud_rules, "未核实的司法项不应产生任何评分规则"


def test_整个非司法维度缺失也必须触发闸门():
    """
    BC-21：旧实现只遍历已经产生 dim_scores 的维度。
    relation 三项全部 unverified 时恰好没有 relation 分数，导致 0% 核实率
    反而绕过闸门。最严重的信息缺口不能比部分缺失更容易获批。
    """
    r = _run(_CLEAN, {
        "guarantee": "unverified",
        "guarantee_circle": "unverified",
        "related_party": "unverified",
    })
    assert "relation" in r["dimensions_excluded"]
    assert any("relation" in g for g in r["gates_applied"])
    assert r["level"] != "低风险"
    assert r["requires_human_review"] is True


# ---------- 一票否决 ----------

def test_失信记录至少高风险():
    c = dict(_CLEAN, judicial_records=[{"type": "失信", "amount": 100}])
    r = _run(c)
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险")
    assert any("失信" in g for g in r["gates_applied"])


def test_被执行记录至少高风险():
    c = dict(_CLEAN, judicial_records=[{"type": "被执行", "amount": 500}])
    r = _run(c)
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险")


def test_作为原告的涉诉不构成负面():
    c = dict(_CLEAN, judicial_records=[
        {"type": "涉诉", "role": "原告", "amount": 200}])
    r = _run(c)
    assert LEVELS.index(r["level"]) < LEVELS.index("高风险"), "原告身份不应触发一票否决"


# ---------- 冲突 ----------

def test_冲突必查项上调一级且强制复核():
    cs = _checks({})
    for c in cs:
        if c["field_id"] == "registration":
            c["status"] = "conflicting"
    base = score(_CLEAN, _checks({}), compute_completeness(_checks({})))
    r = score(_CLEAN, cs, compute_completeness(cs))
    assert LEVELS.index(r["level"]) > LEVELS.index(base["level"]) or r["level"] == "拒绝"
    assert r["requires_human_review"] is True
    assert any("冲突" in g for g in r["gates_applied"])


# ---------- 闸门不可被评分覆盖 ----------

def test_闸门在评分之后施加():
    """即便综合分为 0，闸门仍须生效——这是"不可被评分覆盖"的含义"""
    r = _run(_CLEAN, {"dishonesty": "unverified"})
    assert r["composite_score"] <= 25
    assert r["level"] != "低风险"


def test_综合分会被表现好的维度稀释_故闸门是必需的():
    """
    ⚠️ 这条测试记录的是评分卡的一个**固有性质**，不是缺陷：

    一家已被列为失信被执行人、资产负债率 89.1%、连续亏损、有严重负面舆情的企业，
    综合分只有约 47.5——因为 relation（无对外担保）与 operation（存续）
    这两个"表现好"的维度按权重把分数拉了下来：

        financial  70.0 × 0.30 = 21.0
        judicial   66.7 × 0.30 = 20.0
        relation    0.0 × 0.20 =  0.0   ← 稀释
        operation  15.0 × 0.10 =  1.5
        opinion    50.0 × 0.10 =  5.0
                                 47.5   → 单看分数是"中风险"

    这是加权平均的固有行为。**因此闸门不是锦上添花，而是必需品**——
    真正把这家企业挡住的是"失信 → 至少高风险"这条硬规则，不是分数。

    推论：**综合分不可单独使用**，任何只看分数的下游逻辑都会误判。
    """
    c = {
        "registration": {"operating_status": "存续"},
        "financials": [{"period": "2025年度", "revenue": 9800.0, "net_profit": -2150.0,
                        "debt_ratio": 0.891, "operating_cash_flow": -2870.0}],
        "judicial_records": [{"type": "失信", "amount": 1200},
                             {"type": "被执行", "amount": 1200},
                             {"type": "被执行", "amount": 486}],
        "negative_news": [{"severity": "严重", "subject_confirmed": True}],
        "guarantee": [], "bidding_records": [],
    }
    r = _run(c)
    # 分数本身处在"中风险"区间——这正是问题所在
    assert 25 < r["composite_score"] <= 50, f"实际 {r['composite_score']}"
    # 但闸门把等级顶到了高风险及以上
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险"), \
        "闸门必须弥补加权平均的稀释效应"
    assert any("失信" in g for g in r["gates_applied"])


# ---------- 可解释性 ----------

def test_每条结论都有规则与证据():
    r = _run(_CLEAN)
    assert r["triggered_rules"], "必须能解释分数由哪些规则构成"
    for rule in r["triggered_rules"]:
        assert rule["detail"], "每条规则须有可读说明"
        assert "field_id" in rule, "须能追溯到具体核查项"


def test_闸门留痕():
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    assert r["gates_applied"], "闸门触发必须留痕，否则无法解释等级为何被提升"


def test_评分可复现():
    """纯函数：同一输入必须给出完全相同的结果（对比 LLM 判定的方差）"""
    a = _run(_CLEAN, {"litigation": "unverified"})
    b = _run(_CLEAN, {"litigation": "unverified"})
    assert a["composite_score"] == b["composite_score"]
    assert a["level"] == b["level"]
    assert a["gates_applied"] == b["gates_applied"]


# ---------- 能力缺失 vs 信息缺口（BC-18 决策）----------
#
# 结论：**「低风险不可达」不是缺陷，是正确行为。**
#
# 系统若确实无法核查某个必查项，它本来就不该出具低风险结论。原先的两个候选
# 解法都被否掉了：
#   (a) 把 guarantee_circle 降为选查项 —— 担保圈是监管明确关注的系统性风险，
#       为了让评级好看而改业务定义是自欺
#   (b) 维度核实率分母只计"有数据源"的项 —— 这恰是「查不到 ≠ 没问题」的反面。
#       借款人的担保圈敞口是未知的，不管未知的原因是什么
#
# 真正要修的是**闸门理由说错了**：能力缺失伪装成「这次核实率不足」，
# 导致每份报告都挂同一条闸门。一个永远亮的告警等于没有告警。

def test_能力缺失触发专用闸门而非核实率闸门():
    """⭐ BC-18 的核心行为变更：理由必须准确，否则告警会被学会无视"""
    r = _run(_CLEAN, {"guarantee_circle": "unverified"})
    cap = [g for g in r["gates_applied"] if "尚不具备" in g]
    assert cap, f"必须给出能力缺失的专用闸门：{r['gates_applied']}"
    assert "担保圈" in cap[0], "闸门须点名是哪一项查不了"
    assert "重试也无法解决" in cap[0], "须讲清这不是本次没查到"
    assert not any("relation 维度核实率不足" in g for g in r["gates_applied"]), \
        "能力缺失不该再伪装成维度核实率不足——那是两回事"


def test_能力缺失仍然提升等级下限():
    """
    区分能力缺失**不是**为了把它从风险里排除。
    因为"我们查不了"就不计入风险，正是完整度闸门当初要防的那件事。
    """
    r = _run(_CLEAN, {"guarantee_circle": "unverified"})
    assert r["level"] == "中风险", f"能力缺失必须提升下限，实际 {r['level']}"
    assert r["requires_human_review"], "查不了的项必须转人工"


def test_能力缺失不拖累同维度其它项():
    """
    guarantee 查到了，related_party 也查到了，不该因为担保圈查不了
    就把整个 relation 维度判成"核实率不足"并整体排除出加权。
    """
    comp = compute_completeness(_checks({"guarantee_circle": "unverified"}))
    rel = comp["by_category"]["relation"]
    assert rel["capability_gaps"] == 1
    assert rel["total"] == 2, "能力缺失项不进维度分母"
    assert rel["rate"] == 1.0, f"其余两项都已核实，维度率应为 100%，实际 {rel['rate']}"


def test_能力缺失仍计入总体核实率分母():
    """
    诚实性要求：15 项必查确实只核实了 14 项。
    若把它从总分母里也剔掉，核实率会虚高，总体闸门会被削弱。
    """
    comp = compute_completeness(_checks({"guarantee_circle": "unverified"}))
    assert comp["required_total"] == 15
    assert comp["required_verified"] == 14
    assert comp["verified_rate"] < 1.0, "能力缺失不得让核实率显示为 100%"
    assert "guarantee_circle" in comp["capability_gaps"]
    assert "guarantee_circle" in comp["unverified_fields"], \
        "它同时也是未核实项——两个列表语义不同但可以重叠"


def test_能力就绪后低风险方可达():
    """
    「低风险不可达」的解除条件是**建成关联图谱能力**，
    不是调阈值。这条断言锁住这一点：一旦担保圈真的能查了，规则本身放行。
    """
    assert _run(_CLEAN)["level"] == "低风险", \
        "全部字段（含担保圈）已核实时，规则设计本身应允许低风险"
    assert _run(_CLEAN, {"guarantee_circle": "unverified"})["level"] == "中风险", \
        "担保圈查不了时不得出具低风险"


# ---------- 闸门标识：评测判据不得依赖中文措辞（v0.7）----------

def test_每条闸门都带机器可读标识():
    """
    `gates_applied` 是给人读的，措辞会随可读性调整而变。评测若靠中文子串
    判断闸门理由，就是扫描器同款的开集脆弱性（BC-47）——改一个字断言就假阴性。
    """
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    assert len(r["gate_kinds"]) == len(r["gates_applied"]), \
        "每条闸门说明必须配一个 kind，数量不一致说明有入口漏加"
    assert all(isinstance(k, str) and k for k in r["gate_kinds"])


def test_各类闸门的标识正确():
    from service.risk_scorecard import (
        GATE_CAPABILITY, GATE_CONFLICT, GATE_DISHONESTY_VETO,
        GATE_JUDICIAL_REQUIRED, GATE_OVERALL_RATE,
    )
    jud = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                        "dishonesty": "unverified"})
    assert GATE_JUDICIAL_REQUIRED in jud["gate_kinds"]

    cap = _run(_CLEAN, {"guarantee_circle": "unverified"})
    assert GATE_CAPABILITY in cap["gate_kinds"]

    veto = _run({**_CLEAN, "judicial_records": [{"type": "失信", "amount": 100}]})
    assert GATE_DISHONESTY_VETO in veto["gate_kinds"]

    cs = _checks({})
    for c in cs:
        if c["field_id"] == "registration":
            c["status"] = "conflicting"; c["value"] = None
    conf = score(_CLEAN, cs, compute_completeness(cs))
    assert GATE_CONFLICT in conf["gate_kinds"]

    poor = _run(_CLEAN, {f: "unverified" for f in
                         ("registration", "business_scope", "operating_status",
                          "shareholders", "actual_controller", "revenue",
                          "net_profit", "debt_ratio", "litigation")})
    assert GATE_OVERALL_RATE in poor["gate_kinds"]


def test_不可评级与降级闸门同样带标识():
    from service.risk_scorecard import (
        GATE_PROVENANCE, GATE_UNRATABLE, apply_provenance_gate, unratable,
    )
    u = unratable("测试原因")
    assert u["gate_kinds"] == [GATE_UNRATABLE]

    d = apply_provenance_gate(_run(_CLEAN), [{"field_id": "guarantee", "reason": "x"}])
    assert GATE_PROVENANCE in d["gate_kinds"]
    assert len(d["gate_kinds"]) == len(d["gates_applied"])


def test_人工复核改写也带标识():
    from service.risk_scorecard import (
        GATE_HUMAN_OVERRIDE, GATE_HUMAN_REJECTED, apply_human_review,
    )
    ov = apply_human_review(_run(_CLEAN, {"guarantee_circle": "unverified"}),
                            {"approved": True, "reviewer": "张三",
                             "comment": "已线下核查", "override_level": "低风险"})
    assert GATE_HUMAN_OVERRIDE in ov["gate_kinds"]
    assert len(ov["gate_kinds"]) == len(ov["gates_applied"])

    rj = apply_human_review(_run(_CLEAN), {"approved": False, "reviewer": "李四"})
    assert GATE_HUMAN_REJECTED in rj["gate_kinds"]


def test_能力缺失清单来自配置而非硬编码():
    """新增 not_implemented 项时，闸门应自动覆盖，不需要改评分卡"""
    from config.dd_checklist import CAPABILITY_GAP_IDS, CHECKLIST_BY_ID
    assert CAPABILITY_GAP_IDS == {"guarantee_circle"}, \
        f"当前应只有担保圈一项无数据源，实际 {CAPABILITY_GAP_IDS}"
    for fid in CAPABILITY_GAP_IDS:
        assert CHECKLIST_BY_ID[fid].data_source_status == "not_implemented"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {str(e)[:100]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
