"""
报告断言扫描器 —— 核查清单交叉校验的确定性部分

## 为什么要有这个模块

v0.4 实测：把"报告是否对未核实字段下了结论"整体交给 LLM 判定，
28 例 × 3 次的结果是 **12/18 对照用例不稳定**——同一份输入、同一套提示词，
三次判定结果不一致，且会直接误读清单状态（把 verified 说成 unverified）。
继续加提示词规则产生负收益（见 BADCASES.md BC-14）。

但这个任务的**大部分是机械的**：
    「涉诉记录」状态为未核实，正文出现「无涉诉记录」 → 违规
这是字符串匹配就能判定的事，不需要模型，而且正则的结果零方差、可单测。

因此拆成两段：
    确定性扫描（本模块）—— 字段名与断言词共现，覆盖显式违规
    LLM 判定          —— 只处理不点名字段的隐含断言，如「资信良好、风险可控」

## 判定逻辑

对每个 status != verified 的字段：
  1. 按句切分报告正文
  2. 找出提到该字段的句子（字段名或别名命中）
  3. 若该句同时出现"正确处理标记"（未核实/未能查询/信息缺口…）→ 合规，跳过
  4. 否则若出现"断言词"（无/不存在/未发现/暂无/良好/合规…）→ 判为违规

第 3 步先于第 4 步是关键：「涉诉记录未核实」这句里既有字段名也有"未"字，
必须先识别出正确处理，否则会把规范表述误判为违规。
"""
import re
from typing import Dict, List, Optional

# 字段 → 正文中可能出现的称谓。命中任一即认为该句在谈这个字段。
FIELD_ALIASES: Dict[str, List[str]] = {
    "registration": [
        "工商登记", "注册资本", "实缴资本", "到位资本",
        "统一社会信用代码", "法定代表人", "成立日期",
    ],
    "business_scope": ["经营范围"],
    "operating_status": ["登记状态", "存续", "注销", "吊销", "主体资格"],
    "shareholders": ["股东", "持股", "股权结构"],
    "actual_controller": ["实际控制人", "实控人"],
    "external_investment": ["对外投资"],
    "bidding_record": ["中标", "招投标"],
    "revenue": ["营业收入", "营收"],
    "net_profit": ["净利润"],
    "debt_ratio": ["资产负债率"],
    "cash_flow": ["现金流"],
    "litigation": ["涉诉", "诉讼", "民事纠纷", "开庭"],
    "enforcement": ["被执行"],
    "dishonesty": ["失信"],
    "equity_freeze": ["股权冻结", "股权被冻结"],
    "guarantee": ["对外担保", "担保事项", "保证责任"],
    "guarantee_circle": ["担保圈", "互保", "连环担保"],
    "related_party": ["关联方", "关联交易"],
    "negative_news": ["负面舆情", "负面报道", "负面信息", "舆情"],
    "regulatory_penalty": ["行政处罚", "监管处罚", "处罚记录"],
}

# 正确处理的标记。出现即说明报告在如实披露状态，不构成违规。
# 注意必须先于断言词判定——「涉诉记录未核实」同时含字段名与否定字眼。
COMPLIANT_MARKERS = [
    "未核实", "未能核实", "无法核实", "尚未核实", "待核实",
    "未查询", "未能查询", "未获取", "未覆盖", "不覆盖",
    "信息缺口", "数据源", "接口超时", "建议补充", "补充核查",
    "人工核实", "核实主体", "真实性存疑", "异常信号",
]

# 冲突状态必须明确披露“来源之间存在分歧”；只写成一般性的“待核实”仍会
# 隐去更重要的 conflict 事实。该词表因此与普通未核实白名单分开，避免
# “不同口径但最终无风险”之类句子借冲突词绕过 unverified 断言检查（BC-26）。
CONFLICT_DISCLOSURE_MARKERS = [
    # 「冲突」单独成词即可作为披露标记——只要报告在谈该字段时提到冲突，
    # 就说明它没有掩盖分歧。写成「存在冲突」这类长串会漏掉
    # 「待实缴资本冲突澄清」之类的合规表述（CLN-05 曾因此误报）。
    "冲突", "不一致", "互不相容", "不同口径", "不同记载",
    "并列披露", "各来源", "两者",
    # 以下三词补自 BC-26 的同类延续：留出集修完后仍能构造出
    # 「存在分歧，5000万与1500万并存」这类正确披露被误判的句子。
    # 词表是人工维护的，穷举不可能完备——这里补的是自然中文里
    # 表达"来源之间对不上"最常用的几个说法。
    "分歧", "并存", "两种",
    # 刻意**不收**「存疑」：它可以与"但现已确认为 X"共存，
    # 属于弱披露，收进来会让单方面采信借它绕过检查。
]

# 断言词：对字段实质内容下了结论
NEGATIVE_CLAIMS = [
    "无", "没有", "不存在", "未发现", "未见", "未涉及", "不涉及",
    "暂无", "未查到", "零", "未曾", "从未",
]
EVALUATIVE_CLAIMS = [
    "良好", "正常", "合规", "可控", "较低", "稳健", "健康", "无异常",
    "未见异常", "清晰", "稳定", "扎实", "雄厚", "无瑕疵", "无重大",
]
POSITIVE_CLAIMS = ["存在", "共计", "共有", "涉及"]

_SENT_SPLIT = re.compile(r"[。！？；\n]+")


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _mentions(sentence: str, field_id: str, field_name: str) -> Optional[str]:
    """该句是否在谈这个字段，返回命中的称谓"""
    for alias in FIELD_ALIASES.get(field_id, []) + [field_name]:
        if alias and alias in sentence:
            return alias
    return None


def _claim_in(sentence: str) -> Optional[str]:
    for w in NEGATIVE_CLAIMS + EVALUATIVE_CLAIMS:
        if w in sentence:
            return w
    return None


def scan_report(field_checks: List[Dict], report_text: str) -> List[Dict]:
    """
    扫描报告正文，找出对非 verified 字段的显式断言。

    Returns: [{field_id, field_name, status, issue_type, severity,
               sentence, matched_alias, matched_claim, description}]
    """
    findings: List[Dict] = []
    sents = _sentences(report_text)

    for chk in field_checks or []:
        status = chk.get("status")
        if status == "verified" or status == "not_applicable":
            continue

        fid = chk.get("field_id", "")
        fname = chk.get("field_name", "")

        for sent in sents:
            alias = _mentions(sent, fid, fname)
            if not alias:
                continue

            if status == "conflicting":
                if any(m in sent for m in CONFLICT_DISCLOSURE_MARKERS):
                    continue
                # 冲突字段的规则比未核实更严：**只要谈到它而没披露冲突，就是违规**。
                # 无论采信哪一方都一样——问题不在选了哪个值，而在没告诉读者存在分歧。
                # （早先版本要求"引用了冲突取值"才算，导致 INJ-07 这类
                #   改写数字表述的违规被漏掉。）
                findings.append({
                    "field_id": fid, "field_name": fname, "status": status,
                    "issue_type": "conflict_silently_resolved",
                    "severity": "critical",
                    "sentence": sent[:120], "matched_alias": alias,
                    "matched_claim": _claim_in(sent) or "未披露冲突即作表述",
                    "description": (
                        f"「{fname}」在核查清单中为数据冲突状态，"
                        f"报告在未披露冲突的情况下作出表述：「{sent[:60]}」。"
                        f"必须并列披露各来源取值并指出需人工核实。"
                    ),
                })
                break

            # 普通未核实项先看是否如实披露数据缺口——必须先于断言判定。
            if any(m in sent for m in COMPLIANT_MARKERS):
                continue

            claim = _claim_in(sent)
            if claim:
                findings.append({
                    "field_id": fid, "field_name": fname, "status": status,
                    "issue_type": "unverified_as_fact",
                    "severity": "critical",
                    "sentence": sent[:120], "matched_alias": alias,
                    "matched_claim": claim,
                    "description": (
                        f"「{fname}」在核查清单中为未核实状态，"
                        f"报告却以「{claim}」作出实质性结论：「{sent[:60]}」。"
                        f"未核实既不等于无记录也不等于有记录，"
                        f"正确写法为「该项未核实（原因：{chk.get('failure_reason', '')[:30]}）」。"
                    ),
                })
                break  # 同一字段只报一次

    return findings


def format_findings(findings: List[Dict]) -> str:
    """渲染给 Critic 提示词，说明机械校验已完成"""
    if not findings:
        return (
            "确定性扫描未发现「未核实/冲突字段被显式断言」的情况。\n"
            "（该扫描已覆盖字段名与断言词共现的显式违规，你无需重复检查这类问题。）"
        )
    lines = ["确定性扫描已检出以下显式违规（**已记录，你无需重复报告**）："]
    for f in findings:
        lines.append(f"- [{f['issue_type']}] {f['field_name']}：命中「{f['matched_claim']}」"
                     f"于句「{f['sentence'][:50]}」")
    return "\n".join(lines)
