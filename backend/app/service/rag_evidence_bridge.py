# Copyright © 2026 XbhbxZty
"""把本地知识库中的文本型 PDF 片段接入尽调结构化证据链。

LLM 在本模块里只有一个权限：提出候选。候选必须逐项通过代码校验：

* 结果确实来自当前授权的本地知识库；
* 引文能在被引用片段中逐字找到；
* 主体能在原片段/标题中确认；
* 字段属于固定二十项清单且命中字段关键词；
* 取值或结构化记录的关键值出现在引文中；
* 发布日期由原始结果/URL推导，晚于研究截止日时直接拒绝。

通过后才调用 verification.record_structured_evidence 原子写入。这样 RAG
扩展的是证据来源，而不是绕过原有清单、冲突、截止日与评分闸门。
"""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import re
import unicodedata
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from config.canonical import CANONICAL_MONEY_UNIT, as_number, to_wanyuan
    from config.dd_checklist import CHECKLIST_BY_ID, compute_completeness
    from service.company_profile import replay_from_profile
    from service.risk_scorecard import PROFILE_BACKED_FIELDS
    from service.statement_scope import PARENT, UNKNOWN, resolve_in_chunk
    from service.verification import record_structured_evidence, register_adapter
except ImportError:
    from app.config.canonical import CANONICAL_MONEY_UNIT, as_number, to_wanyuan
    from app.config.dd_checklist import CHECKLIST_BY_ID, compute_completeness
    from app.service.company_profile import replay_from_profile
    from app.service.risk_scorecard import PROFILE_BACKED_FIELDS
    from app.service.statement_scope import PARENT, UNKNOWN, resolve_in_chunk
    from app.service.verification import record_structured_evidence, register_adapter


ADAPTER_ID = "rag_text_document_v1"

_FIELD_TERMS: Dict[str, Tuple[str, ...]] = {
    "registration": ("统一社会信用代码", "注册资本", "法定代表人", "成立日期", "注册地址"),
    "business_scope": ("经营范围", "主要从事", "主营业务"),
    "operating_status": ("登记状态", "经营状态", "存续", "在业", "注销", "吊销"),
    "shareholders": ("股东", "持股", "股本"),
    "actual_controller": ("实际控制人",),
    "external_investment": ("对外投资", "参股", "联营企业", "合营企业"),
    "bidding_record": ("中标", "成交供应商"),
    "revenue": ("营业收入",),
    "net_profit": ("净利润",),
    "debt_ratio": ("资产负债率", "负债合计", "资产总计"),
    "cash_flow": ("经营活动产生的现金流量净额", "经营性现金流"),
    "litigation": ("诉讼", "案件", "案号"),
    "enforcement": ("被执行", "执行标的", "执行案件"),
    "dishonesty": ("失信被执行人", "失信记录"),
    "equity_freeze": ("股权冻结", "司法冻结"),
    "guarantee": ("担保", "被担保方"),
    "guarantee_circle": ("互保", "连环担保", "担保圈"),
    "related_party": ("关联方", "关联交易"),
    "negative_news": ("负面", "事故", "纠纷", "停产", "违约"),
    "regulatory_penalty": ("行政处罚", "监管措施", "处罚决定", "罚款"),
    # —— 场景扩展项（BC-58）。关键词按真实年报语料校准，不凭印象写 ——
    "accounts_receivable_gross": ("应收账款", "账面余额"),
    "accounts_receivable_net": ("应收账款", "账面价值"),
    "accounts_receivable_aging": ("账龄", "1年以内", "1 年以内", "逾期"),
    "accounts_receivable_impairment": ("坏账准备", "信用减值损失", "减值准备"),
    "top5_customer_share": ("前五名客户", "前五大客户", "客户集中度", "合计销售金额"),
    "top5_supplier_share": ("前五名供应商", "前五大供应商", "合计采购金额"),
    "inventory": ("存货",),
    "capex_cash_outflow": ("购建固定资产", "无形资产和其他长期资产支付的现金"),
    "construction_in_progress": ("在建工程",),
    "overseas_revenue": ("境外", "海外", "国外"),
    "guarantee_balance": ("担保余额", "担保总额", "对外担保"),
    "audit_opinion": ("审计意见", "无保留意见", "保留意见"),
    "internal_control_weakness": ("内部控制", "重大缺陷", "内控"),
    "material_litigation": ("重大诉讼", "重大仲裁", "诉讼", "仲裁"),
}

# 场景扩展里的数值型字段。它们要求 period + numeric_value + 原文单位，
# 与核心财务项同样严格；但**不投影到档案、不参与评分**——
# `_FINANCIAL_KEYS` 里的字段会经 `replay_from_profile` 重放并进评分卡，
# 场景项没有对应的打分规则，混进去只会在投影环节抛错。
_SCENARIO_NUMERIC_FIELDS = frozenset({
    "accounts_receivable_gross", "accounts_receivable_net",
    "accounts_receivable_impairment", "inventory", "capex_cash_outflow",
    "construction_in_progress", "overseas_revenue", "guarantee_balance",
})

# 必须取**合并**口径的字段：来自财务报表、且母公司口径会实质改变结论的那些。
#
# 母公司报表含对子公司的内部往来，合并时抵消。实测系统曾把母公司应收账款
# 72,225,597 千元当成公司应收账款（合并口径 66,776,402），高估 8.2%——
# 而对保理业务，那部分内部应收根本不可融。
CONSOLIDATED_REQUIRED_FIELDS = frozenset({
    "revenue", "net_profit", "debt_ratio", "cash_flow",
    "accounts_receivable_gross", "accounts_receivable_net",
    "accounts_receivable_aging", "accounts_receivable_impairment",
    "inventory", "capex_cash_outflow", "construction_in_progress",
    "overseas_revenue", "guarantee_balance", "related_party",
})

_FINANCIAL_KEYS = {
    "revenue": "revenue",
    "net_profit": "net_profit",
    "debt_ratio": "debt_ratio",
    "cash_flow": "operating_cash_flow",
}
_JUDICIAL_TYPES = {"litigation": "涉诉", "enforcement": "被执行", "dishonesty": "失信"}

# 记录型字段的必备关键值。**必须与 `_canonical_record` 的 `_record_values_present`
# 调用保持一致**：前者决定证据窗口要覆盖哪些锚点，后者决定记录能否重建。
# 两处对不上，就会出现"窗口没覆盖某个值 → 记录重建失败"的自相矛盾拒绝。
# 由 `test_record_anchor_keys_match_canonical_record_requirements` 钉住。
_RECORD_KEYS: Dict[str, Tuple[str, ...]] = {
    "litigation": ("case_no", "cause", "amount", "status"),
    "enforcement": ("case_no", "cause", "amount", "status"),
    "dishonesty": ("case_no", "cause", "amount", "status"),
    "guarantee": ("beneficiary", "guarantee_type", "amount"),
    "bidding_record": ("project", "amount", "win_date"),
    "negative_news": ("title", "publish_date", "severity"),
    "regulatory_penalty": ("title", "authority", "publish_date"),
}


def _profile_projector(field_id: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """只从校验后 canonical_records 做确定性投影，绝不读取模型自报 patch。"""
    if raw.get("conflicting") is True:
        return None
    rows = [deepcopy(row) for row in (raw.get("canonical_records") or [])]
    if field_id in _FINANCIAL_KEYS:
        return {"financials": rows}
    if field_id in _JUDICIAL_TYPES:
        return {"judicial_records": rows}
    if field_id == "operating_status":
        return {"registration": {"operating_status": raw.get("canonical_value")}}
    if field_id == "guarantee":
        return {"guarantee": rows}
    if field_id == "bidding_record":
        return {"bidding_records": rows}
    if field_id == "negative_news":
        return {"negative_news": rows}
    if field_id == "regulatory_penalty":
        return {"regulatory_penalty": rows}
    return None


register_adapter(
    ADAPTER_ID,
    "本地知识库文本型文档：原文逐字、主体、字段、取值与截止日均经代码校验",
    project_profile_patch=_profile_projector,
)


def _compact(value: Any) -> str:
    return re.sub(r"[\s,，]", "", str(value or "")).lower()


def _verbatim_compact(value: Any) -> str:
    """逐字定位时忽略排版空白与标点，但不忽略任何字母、汉字或数字。"""
    return "".join(
        char.lower() for char in str(value or "")
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))
    )


def _is_verbatim_char(char: str) -> bool:
    return not char.isspace() and not unicodedata.category(char).startswith(("P", "S"))


def _compact_with_offsets(text: str) -> Tuple[str, List[int]]:
    """返回 (逐字压缩串, 压缩位→原文位 映射)。

    `_verbatim_compact` 只能回答"在不在"，无法回答"在哪"。系统要自己从原文
    切出证据窗口，就必须把压缩空间里的命中位置映射回原文下标——否则切出来
    的片段不再是原文的连续子串，逐字保证当场失效。
    """
    chars: List[str] = []
    offsets: List[int] = []
    for index, char in enumerate(text):
        if _is_verbatim_char(char):
            chars.append(char.lower())
            offsets.append(index)
    return "".join(chars), offsets


def _locate_verbatim(text: str, needle: Any) -> Optional[Tuple[int, int]]:
    """在原文中定位 needle 的逐字位置，忽略排版空白与标点。

    返回原文坐标 [start, end)；定位不到返回 None。
    """
    compact_needle = _verbatim_compact(needle)
    if not compact_needle:
        return None
    haystack, offsets = _compact_with_offsets(text)
    position = haystack.find(compact_needle)
    if position < 0:
        return None
    return offsets[position], offsets[position + len(compact_needle) - 1] + 1


# 证据窗口上限。此前这个数字是**提示词里对模型的要求**（"exact_quote 最多
# 500 个字符"），模型抄不出来就整条候选被拒。现在窗口由系统切，这个上限
# 变成系统自己的裁剪预算：切不出来是可诊断的结构问题，不再是模型的错。
QUOTE_MAX_CHARS = 600

# 记录型字段的多个关键值必须落在同一个窗口里。跨度超过这个值说明它们在
# 原文里根本不属于同一行/同一段，硬拼成一段引文就不再是原文。
RECORD_SPAN_MAX_CHARS = 900


class QuoteWindow:
    """系统从原文切出的连续证据窗口。

    `quote` **一定**是 `source_text` 的连续子串——它是切出来的，不是模型抄
    出来的。BC-57 的根因就是把这件事交给了模型：五个要素（主体、字段名、
    期间、数值、单位）在真实年报的扁平化表格里不在同一个连续窗口内，
    模型唯一能做的就是拼接，而拼接产物必然过不了逐字校验。
    """

    __slots__ = ("quote", "start", "end", "covered_terms")

    def __init__(self, quote: str, start: int, end: int, covered_terms: Tuple[str, ...]):
        self.quote = quote
        self.start = start
        self.end = end
        self.covered_terms = covered_terms


def _expand_to_boundaries(text: str, start: int, end: int, budget: int) -> Tuple[int, int]:
    """在预算内把窗口向两侧扩到行/句边界，让引文读起来是完整的一句或一行。"""
    boundaries = "\n。；;！!？?"
    left = start
    while left > 0 and (start - left) < budget // 2 and text[left - 1] not in boundaries:
        left -= 1
    right = end
    while right < len(text) and (right - end) < budget // 2 and text[right] not in boundaries:
        right += 1
    if right < len(text) and text[right] in "。；;！!？?":
        right += 1
    return left, right


def _build_quote_window(
    source_text: str,
    anchors: Iterable[Any],
    terms: Iterable[str] = (),
) -> Tuple[Optional[QuoteWindow], str]:
    """由系统从原文切出证据窗口。

    Args:
        source_text: 被引用片段的**完整**原文
        anchors: 必须全部落在窗口内的值（字段取值、记录关键值）
        terms: 尽量纳入的字段关键词；纳入不了不算失败，但会如实报告

    Returns:
        (窗口, 失败原因)。成功时失败原因为空串。
    """
    spans: List[Tuple[int, int]] = []
    for anchor in anchors:
        if anchor in (None, ""):
            continue
        located = _locate_verbatim(source_text, anchor)
        if located is None:
            return None, f"取值「{str(anchor)[:24]}」无法在原文中逐字定位"
        spans.append(located)
    if not spans:
        return None, "候选未给出任何可定位的取值"

    start, end = min(s for s, _ in spans), max(e for _, e in spans)
    if end - start > RECORD_SPAN_MAX_CHARS:
        return None, f"记录要素在原文中跨度 {end - start} 字符，无法落在同一段连续原文内"

    covered: List[str] = []
    # 行标签通常在数字**之前**（扁平化表格里 "营业收入 362,012,554 ..."），
    # 所以优先向左找关键词，找不到再向右。
    for term in terms:
        if _verbatim_compact(term) in _verbatim_compact(source_text[start:end]):
            covered.append(term)
            continue
        located = _locate_verbatim(source_text[:start], term)
        if located is not None and start - located[0] <= QUOTE_MAX_CHARS:
            best = _locate_verbatim(source_text[max(0, start - QUOTE_MAX_CHARS):start], term)
            if best is not None:
                offset = max(0, start - QUOTE_MAX_CHARS)
                start = offset + best[0]
                covered.append(term)
                continue
        tail_zone = source_text[end:end + QUOTE_MAX_CHARS]
        located = _locate_verbatim(tail_zone, term)
        if located is not None:
            end = end + located[1]
            covered.append(term)

    if end - start > QUOTE_MAX_CHARS:
        start, end = spans[0][0], max(e for _, e in spans)
        covered = [t for t in covered
                   if _verbatim_compact(t) in _verbatim_compact(source_text[start:end])]

    budget = max(0, QUOTE_MAX_CHARS - (end - start))
    start, end = _expand_to_boundaries(source_text, start, end, budget)
    quote = source_text[start:end].strip()
    if not quote:
        return None, "原文窗口为空"
    return QuoteWindow(quote, start, end, tuple(covered)), ""


def _date_only(value: str) -> str:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", value or "")
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


def _source_date(result: Dict[str, Any]) -> str:
    """日期只能来自检索结果本体；禁止采信 LLM 返回的 publication_date。"""
    for value in (result.get("date"), result.get("publication_date")):
        parsed = _date_only(str(value or ""))
        if parsed:
            return parsed
    text = str(result.get("summary") or result.get("snippet") or "")
    for pattern in (
        r"(?:发布日期|发布日|公告日期)[:：]\s*([^\n]{4,20})",
        r"来源：[^\n]*?/(20\d{2}[-/]\d{1,2}[-/]\d{1,2})",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _date_only(match.group(1))
            if parsed:
                return parsed
    return _date_only(str(result.get("url") or ""))


def _subject_aliases(subject: str) -> List[str]:
    """主体别名。逐层剥离组织形式与行业词，保留字号。

    只剥后缀、不做前缀截断：`宁德时代新能源科技股份有限公司` 会产出
    `…新能源科技` 和 `宁德时代`，但绝不产出 `宁德` 这种两字前缀——
    别名越短，把另一家同字号企业的材料认成本主体的风险越高。
    """
    aliases = [subject]
    stripped = re.sub(r"(?:股份有限公司|有限责任公司|有限公司|集团|公司)$", "", subject)
    if len(stripped) >= 4 and stripped != subject:
        aliases.append(stripped)
    # 再剥一层行业词，得到字号。年报封面用全称，发布机构行常只写字号。
    core = re.sub(
        r"(?:新能源科技|新能源|科技集团|科技|实业|控股|股份|集团)+$", "", stripped
    )
    if len(core) >= 4 and core not in aliases:
        aliases.append(core)
    return [_compact(alias) for alias in aliases if alias]


# 片段头部由入库流程写入，携带**文档级**身份（来源编号、定位符、文档标题、
# 发布机构、原始 URL）。主体归属是文档的属性，不是某一页表格的属性。
_HEADER_ZONE_CHARS = 400


def _document_identity_zone(result: Dict[str, Any], source_text: str) -> str:
    """供主体确认使用的**文档级**标识文本。

    BC-57 的一处假拒绝就出在这里：原实现要求主体名出现在片段正文里。
    真实年报的财务附注页、单位表头页根本不写公司全称；港交所招股书的
    标题还是繁体（`寧德時代…`），简体别名子串匹配必然失败。
    于是"这一页没写公司名"被记成了"无法确认属于本主体"。

    主体应当由**文档**确认一次：片段头部的标题/发布机构/来源 URL、检索结果
    的标题与 URL 共同构成文档身份。真正的隔离边界是 `kb_scope`（只有本次
    授权知识库的文档可被检索到），不是逐页复述公司名。
    """
    return _compact("\n".join((
        source_text[:_HEADER_ZONE_CHARS],
        str(result.get("title") or ""),
        str(result.get("url") or ""),
        str(result.get("site_name") or ""),
        str(result.get("doc_name") or ""),
    )))


def _resolve_financial_unit(
    source_text: str, quote: str, numeric: Any
) -> Tuple[str, str]:
    """确定性解析财务单位；**不采信模型自报单位**。

    单位是表格/文档级属性，不是数据行的属性——年报把 `单位：千元` 写在表头，
    数据行只有数字。原实现要求单位与数值出现在同一段引文里，做不到就整条
    拒绝；实测语料里 `单位：千元` 出现 203 次，全部在表头行。

    优先级：窗口内 → 数值近邻 → 整段原文中数值**之前**最近的单位表头。
    返回 (单位, 单位取证原文)；取不到返回 ("", "")。
    """
    inside = _financial_unit_from_quote(quote, numeric)
    if inside:
        return inside, quote

    located = _locate_verbatim(source_text, numeric) if numeric not in (None, "") else None
    cutoff = located[0] if located else len(source_text)
    header_pattern = re.compile(r"单位\s*[:：]\s*(亿元|万元|千元|元)|(?:人民币)(千元|万元|元)")
    last: Optional[re.Match] = None
    for match in header_pattern.finditer(source_text, 0, cutoff):
        last = match
    if last:
        unit = last.group(1) or last.group(2)
        line_start = source_text.rfind("\n", 0, last.start()) + 1
        line_end = source_text.find("\n", last.end())
        line_end = len(source_text) if line_end < 0 else line_end
        return unit, source_text[line_start:line_end].strip()
    return "", ""


def _source_identity(result: Dict[str, Any], source_id: str, locator: str) -> str:
    base = str(result.get("url") or result.get("title") or "local-document")
    suffix = "/".join(part for part in (source_id, locator) if part)
    return f"{base}#{suffix}" if suffix else base


def _header_metadata(text: str) -> Tuple[str, str]:
    match = re.search(
        r"\[case_id=[^;\]]+;\s*source_id=([^;\]]+);\s*locator=([^\]]+)\]",
        text,
    )
    return (match.group(1).strip(), match.group(2).strip()) if match else ("", "")


def _reject(
    state: Dict[str, Any],
    reason: str,
    candidate: Dict[str, Any],
    section_id: str,
    detail: str = "",
) -> None:
    """记一条候选拒绝。

    ⚠️ `reason` 必须是**稳定的类别**，具体取值走 `detail`。

    此前若干处把取值拼进了 reason（`f"…（取值「{value}」无法定位）"`），
    于是同一类失败在统计里碎成十几个各计数 1 的条目——`rejection_reasons`
    对最常见的那一类完全失去了聚合能力，也就无法回答"最大的卡点是什么"。
    实测 A 轮 20 条拒绝里有 12 条是这样各自成行的。

    类别与明细分开之后，计数按类别聚合，明细留作样本供归因。
    """
    state.setdefault("rag_evidence_rejections", []).append({
        "reason": reason,
        "detail": str(detail or "")[:160],
        "field_id": candidate.get("field_id", ""),
        "value": str(candidate.get("value") or "")[:80],
        "section_id": section_id,
        "source_result_index": candidate.get("source_result_index"),
    })


def _validate_source(
    state: Dict[str, Any],
    candidate: Dict[str, Any],
    results: List[Dict[str, Any]],
    section_id: str,
) -> Optional[Dict[str, Any]]:
    """校验候选指向的**来源**是否可用；不再校验模型抄来的引文。

    模型在新契约下只交锚点（`source_result_index` + 取值 + 期间 + record），
    引文由 `_build_quote_window` 从原文切出。所以这一层只回答三个问题：
    这个来源在授权范围内吗、它属于本尽调主体吗、它的日期没越过截止日吗。
    """
    try:
        index = int(candidate.get("source_result_index")) - 1
    except (TypeError, ValueError):
        _reject(state, "source_result_index 非法", candidate, section_id)
        return None
    if index < 0 or index >= len(results):
        _reject(state, "source_result_index 越界", candidate, section_id)
        return None
    result = results[index]
    if not result.get("is_local"):
        _reject(state, "当前版本只允许本地知识库文本文件进入结构化证据链", candidate, section_id)
        return None
    source_text = str(result.get("summary") or result.get("snippet") or "")
    if not source_text.strip():
        _reject(state, "被引用片段没有可用原文", candidate, section_id)
        return None

    subject = str(state.get("company_name") or state.get("subject_name") or "").strip()
    if subject:
        identity = _document_identity_zone(result, source_text)
        if not any(alias in identity for alias in _subject_aliases(subject)):
            _reject(state, "来源文档无法确认属于当前尽调主体", candidate, section_id)
            return None

    publication_date = _source_date(result)
    cutoff = str(state.get("as_of") or "")[:10]
    if publication_date and cutoff and publication_date > cutoff:
        _reject(state, "来源日期晚于研究截止日", candidate, section_id,
                f"{publication_date} > {cutoff}")
        return None

    source_id, locator = _header_metadata(source_text)
    return {
        "result": result,
        "source_text": source_text,
        "publication_date": publication_date,
        "source_id": source_id,
        "locator": locator,
        "source": _source_identity(result, source_id, locator),
        "section_id": section_id,
    }


def _record_values_present(record: Dict[str, Any], keys: Iterable[str], quote: str) -> bool:
    compact_quote = _compact(quote)
    for key in keys:
        value = record.get(key)
        if value in (None, "") or _compact(value) not in compact_quote:
            return False
    return True


def _financial_unit_from_quote(quote: str, numeric: Any) -> str:
    """从原文表头或数字后缀取单位，禁止采信模型自报单位。"""
    text = str(quote or "")
    header = re.search(r"(?:单位\s*[:：]\s*|[（(])\s*(亿元|万元|千元|元|%)\s*[）)]?", text)
    if header:
        return header.group(1)
    raw_numeric = str(numeric or "").strip()
    if raw_numeric:
        match = re.search(re.escape(raw_numeric).replace(r"\,", "[,]?"), text)
        if match:
            nearby = text[match.end():match.end() + 12]
            suffix = re.search(r"^[\s,，、:：;；-]*(亿元|万元|千元|元|%)", nearby)
            if suffix:
                return suffix.group(1)
    return ""


def _canonical_record(field_id: str, candidate: Dict[str, Any], quote: str) -> Optional[Dict[str, Any]]:
    record = candidate.get("record") if isinstance(candidate.get("record"), dict) else {}
    if field_id in _JUDICIAL_TYPES:
        if not _record_values_present(record, ("case_no", "cause", "amount", "status"), quote):
            return None
        return {
            "type": _JUDICIAL_TYPES[field_id], "case_no": record["case_no"],
            "cause": record["cause"], "amount": record["amount"],
            "unit": record.get("unit") or "万元", "status": record["status"],
            "role": record.get("role") or "",
        }
    if field_id == "guarantee":
        if not _record_values_present(record, ("beneficiary", "guarantee_type", "amount"), quote):
            return None
        return {
            "beneficiary": record["beneficiary"], "guarantee_type": record["guarantee_type"],
            "amount": record["amount"], "unit": record.get("unit") or "万元",
            "period": record.get("period") or "期限未载明",
            "board_resolution": record.get("board_resolution") or "内部决议情况未载明",
        }
    if field_id == "bidding_record":
        if not _record_values_present(record, ("project", "amount", "win_date"), quote):
            return None
        return {"project": record["project"], "amount": record["amount"],
                "unit": record.get("unit") or "万元", "win_date": record["win_date"]}
    if field_id == "negative_news":
        if not _record_values_present(record, ("title", "publish_date", "severity"), quote):
            return None
        return {"title": record["title"], "publish_date": record["publish_date"],
                "severity": record["severity"], "source": record.get("source") or "本地文档",
                "summary": record.get("summary") or quote, "subject_confirmed": True}
    if field_id == "regulatory_penalty":
        if not _record_values_present(record, ("title", "authority", "publish_date"), quote):
            return None
        row = {"title": record["title"], "authority": record["authority"],
               "publish_date": record["publish_date"], "subject_confirmed": True}
        if record.get("amount") not in (None, ""):
            if _compact(record["amount"]) not in _compact(quote):
                return None
            row.update(amount=record["amount"], unit=record.get("unit") or "万元")
        return row
    return None


def collect_analysis_evidence(
    state: Dict[str, Any],
    analysis: Dict[str, Any],
    results: List[Dict[str, Any]],
    section_id: str,
) -> None:
    """校验一次 Scout 分析；事实与字段候选采用相同的来源/主体/日期闸门。

    ## 契约（BC-57 重画）

    模型**只提出锚点**：引用哪一条结果、字段是什么、取值/记录/期间是什么。
    引文由系统用 `_build_quote_window` 从原文切出，`exact_quote` 不再由模型
    回抄——模型擅长语义定位，不擅长充当无损字符串传输层。

    模型若仍在载荷里带了 `exact_quote`，一律**忽略**：接口改造不能顺手开出
    一条新的信任路径。
    """
    if not state.get("due_diligence_mode"):
        return

    # 原文定位只证明“这是原文”，还不等于对应清单字段已经核实。先把它标成
    # RAG 候选；finalize 会在字段完成度/冲突检查后，只放行属于 verified
    # 字段的事实。否则一条不完整财务片段会从 facts 侧门进入报告。
    for candidate in analysis.get("extracted_facts", []):
        common = _validate_source(state, candidate, results, section_id)
        if not common:
            continue
        content = str(candidate.get("content") or "").strip()
        if len(_verbatim_compact(content)) < 6:
            _reject(state, "事实 content 过短，无法作为原文锚点", candidate, section_id)
            continue
        window, failure = _build_quote_window(common["source_text"], [content])
        if window is None:
            _reject(state, "事实内容无法在原文中定位", candidate, section_id, failure)
            continue
        common = {**common, "quote": window.quote}
        url = str(common["result"].get("url") or "")
        matched = next((fact for fact in reversed(state.get("facts", []))
                        if fact.get("content") == content), None)
        if matched:
            matched["verified"] = False
            matched["rag_evidence_candidate"] = True
            matched["source_url"] = url
            matched["source_name"] = common["result"].get("site_name") or matched.get("source_name")
            # 来源等级不能继续采信模型自报分数。普通知识库上传件统一按“企业
            # 提交材料，审计状态未由系统确认”处理；日后接文档元数据再细分。
            matched["credibility_score"] = 0.6
            matched["source_type"] = "local_document"
            matched["metadata"] = {
                **(matched.get("metadata") or {}),
                "origin": "rag_text_document",
                "source_id": common["source_id"],
                "locator": common["locator"],
                "exact_quote": common["quote"],
                "as_of_date": common["publication_date"],
            }

    for candidate in analysis.get("field_evidence", []):
        field_id = str(candidate.get("field_id") or "")
        if field_id not in CHECKLIST_BY_ID:
            _reject(state, "field_id 不属于固定二十项清单", candidate, section_id)
            continue
        common = _validate_source(state, candidate, results, section_id)
        if not common:
            continue
        source_text = common["source_text"]
        value = candidate.get("value")
        if value in (None, ""):
            _reject(state, "候选未给出字段取值", candidate, section_id)
            continue

        # 窗口锚点：字段取值 + 财务数值 + record 的全部关键值。
        # 它们必须**同时**落在一段连续原文里，否则这不是一条可引用的记录。
        anchors: List[Any] = [value]
        if field_id in _FINANCIAL_KEYS and candidate.get("numeric_value") not in (None, ""):
            anchors.append(candidate.get("numeric_value"))
        record_keys = _RECORD_KEYS.get(field_id, ())
        raw_record = candidate.get("record") if isinstance(candidate.get("record"), dict) else {}
        anchors.extend(raw_record.get(key) for key in record_keys
                       if raw_record.get(key) not in (None, ""))

        window, failure = _build_quote_window(source_text, anchors, _FIELD_TERMS[field_id])
        if window is None:
            _reject(state, "无法从原文切出证据窗口", candidate, section_id, failure)
            continue
        quote = window.quote
        if not window.covered_terms:
            # 与"模型改写"分开报告：这里模型没有做错任何事，是取值附近
            # 确实没有该字段的确定性关键词，属于字段归属无法确认。
            _reject(state, "取值近邻原文没有该字段的确定性关键词，字段归属无法确认",
                    candidate, section_id)
            continue
        accepted = {**deepcopy(candidate), **common, "field_id": field_id, "quote": quote}
        # 模型自报的引文一律丢弃：接口改造不得留下新的信任路径。
        accepted.pop("exact_quote", None)
        accepted.pop("source_text", None)

        if field_id in _FINANCIAL_KEYS:
            period = str(candidate.get("period") or "").strip()
            numeric = candidate.get("numeric_value")
            if not period or numeric in (None, ""):
                _reject(state, "财务候选缺少 period/numeric_value", candidate, section_id)
                continue
            try:
                accepted["numeric_value"] = float(str(numeric).replace(",", ""))
            except ValueError:
                _reject(state, "numeric_value 不是数值", candidate, section_id)
                continue
            unit, unit_quote = _resolve_financial_unit(source_text, quote, numeric)
            if not unit:
                _reject(state, "原文中找不到该数值适用的单位表头", candidate, section_id)
                continue
            accepted["unit"] = unit
            # 单位取自表头时，取证原文与数值不在同一窗口。两段都留档，
            # 附录才能回答"这个万元是从哪一行读出来的"。
            accepted["unit_quote"] = unit_quote
        elif field_id in _SCENARIO_NUMERIC_FIELDS:
            # 场景数值项：期间与单位同样必须来自原文，只是不投影进档案。
            period = str(candidate.get("period") or "").strip()
            numeric = candidate.get("numeric_value")
            if not period or numeric in (None, ""):
                _reject(state, "场景数值候选缺少 period/numeric_value", candidate, section_id)
                continue
            try:
                accepted["numeric_value"] = float(str(numeric).replace(",", ""))
            except ValueError:
                _reject(state, "numeric_value 不是数值", candidate, section_id)
                continue
            unit, unit_quote = _resolve_financial_unit(source_text, quote, numeric)
            if not unit:
                _reject(state, "原文中找不到该数值适用的单位表头", candidate, section_id)
                continue
            accepted["unit"] = unit
            accepted["unit_quote"] = unit_quote
        elif field_id in PROFILE_BACKED_FIELDS and field_id != "operating_status":
            record = _canonical_record(field_id, candidate, quote)
            if record is None:
                _reject(state, "评分字段缺少可由原文重建的结构化 record", candidate, section_id)
                continue
            accepted["canonical_record"] = record

        # —— 报表口径闸门 ——
        #
        # 放在最后：前面每一道（逐字、主体、单位、期间、record）都过了，
        # 这一道才是唯一能拦住"数字真实但取自母公司报表"的。实测该缺陷
        # 高估应收账款 8.2%，而所有既有闸门全部放行。
        #
        # `unknown` 放行：它只可能出现在文档第一个分节标题**之前**，即年报
        # 正文/摘要/公告——那里按 A 股披露惯例讨论的就是合并口径，母公司
        # 数据不会出现在那儿。但口径仍如实记录，附录要能回答"凭什么算合并"。
        # 用窗口起点做位置：它由 `_locate_verbatim` 逐字定位得出，
        # 比对 source_text 重新 find 更准（原文里有插入的空白与全半角差异）。
        scope = resolve_in_chunk(
            str(common["result"].get("statement_scope") or UNKNOWN),
            common["result"].get("statement_scope_marks") or [],
            window.start,
        )
        if field_id in CONSOLIDATED_REQUIRED_FIELDS and scope == PARENT:
            _reject(state, "取自母公司报表，该字段要求合并口径", candidate, section_id,
                    "母公司口径含对子公司内部往来，合并时抵消，不可用于本字段")
            continue
        accepted["statement_scope"] = scope
        state.setdefault("rag_evidence_candidates", []).append(accepted)


def _to_wanyuan(value: float, unit: str) -> float | int:
    """换算到万元。口径定义在 `config.canonical`，本函数只做序列化适配。

    此前这里是本模块自己的一张 float 因子表，与评分器里的 `Decimal(v)/10`
    构成同一规则的两份实现（BC-59 的根因）。现在两侧共用一处定义。
    """
    converted = to_wanyuan(value, unit)
    if converted is None:
        # 非金额单位（如百分比）不做换算，原值返回，与旧行为一致。
        return int(value) if float(value).is_integer() else round(float(value), 4)
    return as_number(converted)


def _financial_comparable(field_id: str, row: Dict[str, Any]) -> Decimal:
    """把财务候选归一到同一口径后再判断冲突。

    返回 Decimal：这个值要参与**相等判断**（同期间多源是否异值），
    浮点尾差会把同一个金额判成冲突。
    """
    raw = Decimal(str(row["numeric_value"]))
    unit = str(row.get("unit") or "")
    if field_id == "debt_ratio":
        is_percent = "%" in unit or "%" in str(row.get("value") or "")
        return raw / Decimal("100") if is_percent else raw
    converted = to_wanyuan(raw, unit or CANONICAL_MONEY_UNIT)
    return converted if converted is not None else raw


def _profile_display(field_id: str, patch: Dict[str, Any], checks: List[Dict[str, Any]]) -> str:
    company = {"name": "RAG证据重放", **deepcopy(patch),
               "coverage": {"queried": [field_id]}}
    predicted = replay_from_profile(company, checks).get(field_id) or {}
    if predicted.get("status") != "verified" or predicted.get("value") in (None, ""):
        raise ValueError(f"字段 {field_id} 的 profile_patch 无法经生产映射重放")
    return predicted["value"]


def _deduplicate(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for candidate in candidates:
        key = (candidate.get("field_id"), candidate.get("source"), candidate.get("period"),
               _compact(candidate.get("value")), _compact(candidate.get("quote")))
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _term_in_quote(term: str, text: str) -> bool:
    """完整度判据的措辞匹配，与字段关键词闸门**同一口径**。

    PDF 抽取会在词内插入空白：实测 `1年以内` 在 case_01 语料出现 0 次，
    `1 年以内` 出现 11 次；`2024 年年度报告` 也是这么来的。字段关键词闸门走
    `_verbatim_compact` 所以不受影响，而完整度判据原来用裸 `in`——
    同一份原文，两处判据给出不同答案，且不一致的那一处会让该项永远不完整。

    与 BC-60 同因：判据里的字面量必须按真实文档校准，**并且**匹配方式要和
    别处一致。"我们已经处理过这个问题"处理的是那一个入口。
    """
    return _verbatim_compact(term) in _verbatim_compact(text)


def _field_is_complete_enough(field_id: str, rows: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """“命中一个事实”不等于“整项核查完成”；在没有 partial 状态时宁可保守。"""
    if field_id in _FINANCIAL_KEYS:
        periods = {str(row.get("period") or "") for row in rows if row.get("period")}
        if len(periods) < 3:
            return False, f"已取得 {len(periods)} 个期间的明确数值，固定清单要求近三期"
        return True, ""
    if field_id == "registration":
        required = ("统一社会信用代码", "注册资本", "成立", "法定代表人", "注册地址", "企业类型")
        hit = {term for row in rows for term in required
               if _term_in_quote(term, row.get("quote", ""))}
        if len(hit) < 5:
            return False, f"工商基本信息仅覆盖 {len(hit)}/6 个关键要素"
    if field_id == "shareholders":
        combined = "\n".join(str(row.get("quote") or "") for row in rows)
        # 语料校准（BC-60）：真实年报写「前十名股东」，`前10名股东` 与
        # `全部股东` 在 case_01 全语料里各出现 0 次。按未校准的写法，
        # 这一项**永远**无法判定完整——两条只存在于我脑子里的措辞，
        # 成了系统性失明的原因，而且失效是静默的：failure_reason 写
        # 「仅命中个别持股事实」，读起来完全像一次正常的信息缺口。
        roster_terms = ("前十名股东", "前10名股东", "全部股东", "股东总数")
        if not _term_in_quote("股东名称", combined) \
                or not any(_term_in_quote(t, combined) for t in roster_terms):
            return False, "仅命中个别持股事实，不能据此认定完整股东结构已核实"
    if field_id == "business_scope" and max((len(str(row.get("value") or "")) for row in rows), default=0) < 12:
        return False, "经营范围候选过短，无法确认完整登记范围"
    if field_id in {"external_investment", "related_party"}:
        # 列表型字段需要专门的全量表格解析；当前片段命中仍可作为 verified fact
        # 写进报告，但不抬高清单核实率。
        return False, "检索到局部记录，但文本片段不足以证明列表已完整核查"
    return True, ""


def _rejection_breakdown(rejections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按「原因 × 字段」聚合拒绝，并保留取值样本。

    ## 为什么光有计数不够

    只导出 `rejection_reasons` 的计数时，"最大卡点是字段关键词近邻检查
    （10 条）"这句话之后就断了：无法回答是哪几个字段、模型提交的是什么值、
    因而无法判断是闸门太严还是候选本就该拒。逐条明细留在 state 里没有导出，
    终局事件、评测器、前端都看不到——**关键判据的失败原因不可归因**。

    样本只留少量且截断：这是诊断信息，不是审计记录；审计走证据附录。
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in rejections:
        reason = str(item.get("reason") or "未知原因")
        row = grouped.setdefault(reason, {"reason": reason, "count": 0,
                                          "fields": Counter(), "samples": []})
        row["count"] += 1
        row["fields"][str(item.get("field_id") or "(未指定)")] += 1
        if len(row["samples"]) < 5:
            sample = {key: value for key, value in (
                ("field_id", str(item.get("field_id") or "")),
                ("value", str(item.get("value") or "")),
                ("detail", str(item.get("detail") or "")),
                ("section_id", str(item.get("section_id") or "")),
            ) if value}
            if sample:
                row["samples"].append(sample)
    return [
        {**row, "fields": dict(row["fields"])}
        for row in sorted(grouped.values(), key=lambda entry: -entry["count"])
    ]


def _profile_numeric(field_id: str, company: Dict[str, Any], period: str) -> Optional[Decimal]:
    """档案中**同期间**的规范化取值；取不到返回 None（= 无法比较，不是不一致）。

    与 `_financial_comparable` 归到同一口径（金额→万元、比率→小数），
    两侧才可以做相等判断。
    """
    key = _FINANCIAL_KEYS.get(field_id)
    if not key or not period:
        return None
    target = _compact(period)
    for row in company.get("financials") or []:
        if _compact(row.get("period")) != target:
            continue
        value = row.get(key)
        if value in (None, ""):
            return None
        raw = Decimal(str(value))
        if field_id == "debt_ratio":
            # 档案存的是小数（0.6328）；若有人存成 63.28 也归一到小数
            return raw / Decimal("100") if raw > 1 else raw
        converted = to_wanyuan(raw, str(row.get("unit") or CANONICAL_MONEY_UNIT))
        return converted if converted is not None else raw
    return None


def _cross_channel_conflict(
    field_id: str, rows: List[Dict[str, Any]], company: Dict[str, Any]
) -> Tuple[Optional[str], str]:
    """既有结论与 RAG 候选是否**确实**矛盾。

    返回 (冲突说明 | None, 无法判定的原因 | "")。

    ## 为什么不能直接比字符串（BC-68）

    原实现是：

        _compact(row["value"]) != _compact(check["value"])

    左边是文档里的一个单元格（`"501,200"`），右边是档案渲染出来的展示字符串
    （`"2023年度 41250.0万元；2024年度 46800.0万元；2025年度 50120.0万元"`）。
    **这两个永远不可能相等**——与单位归一无关，归一了也还是不可比。
    于是只要某字段同时被档案和 RAG 覆盖，就必然报"不一致"：实测一次完整
    运行 4 个重叠字段全部误报，误报率 100%。

    后果不是多几条噪声：这是**跨通道一致性检查**，本该抓真实的档案-文档矛盾
    （档案写注册资本 8000 万、文档写 5000 万那种核心风险线索）。永远为真的
    告警会让复核人学会忽略它，真矛盾出现时一并被忽略——与"假故障淹没真故障"
    同一个道理。

    ## 现在的判据

    只在能**正面确立**不一致时才报：同一字段、同一期间、两侧都有可归一的
    数值，且归一后不等。比不了的一律不报，另记入 `cross_channel_undecidable`
    ——"无法判定"既不是"一致"也不是"矛盾"，把它藏起来等于重犯 BC-51。
    """
    if field_id not in _FINANCIAL_KEYS:
        return None, "非数值字段：档案存展示字符串、RAG 存单元格取值，两者不可比"

    undecidable = ""
    for row in rows:
        period = str(row.get("period") or "")
        if row.get("numeric_value") in (None, ""):
            undecidable = undecidable or "RAG 候选没有可归一的数值"
            continue
        expected = _profile_numeric(field_id, company, period)
        if expected is None:
            undecidable = undecidable or f"档案中没有期间 {period or '（未标注）'} 的同口径取值"
            continue
        actual = _financial_comparable(field_id, row)
        if actual != expected:
            return (f"期间 {period}：既有结论 {expected}，RAG 检出 {actual}"
                    f"（均已归一到 {CANONICAL_MONEY_UNIT}/比率口径）"), ""
    return None, undecidable


def finalize_rag_evidence(state: Dict[str, Any]) -> Dict[str, Any]:
    """把已校验候选汇总成字段证据；同期间多源异值显式标为冲突。"""
    if not state.get("due_diligence_mode"):
        return {"verified": 0, "conflicting": 0, "rejected": 0}
    checks = state.get("field_checks") or []
    check_by_id = {check.get("field_id"): check for check in checks}
    store = state.setdefault("evidence_store", {})
    candidates = _deduplicate(state.get("rag_evidence_candidates") or [])
    retrieved_at = datetime.now(timezone.utc).isoformat()
    rejections = [item for item in (state.get("rag_evidence_rejections") or [])
                  if isinstance(item, dict)]
    rejection_reasons = Counter(
        str(item.get("reason") or "未知原因") for item in rejections
    )
    counts = {"verified": 0, "conflicting": 0,
              "rejected": len(rejections),
              "rejection_reasons": dict(rejection_reasons),
              "rejection_breakdown": _rejection_breakdown(rejections)}

    for field_id in CHECKLIST_BY_ID:
        rows = [candidate for candidate in candidates if candidate.get("field_id") == field_id]
        check = check_by_id.get(field_id)
        if not rows or not check:
            continue
        if check.get("status") != "unverified":
            # 初始档案/真实结构化适配器已有结论时不按调用顺序覆盖。
            # 但"不覆盖"不等于"不比对"：两条通道对同一字段各有取值时，
            # 真矛盾是核心风险线索，必须让复核人看到（BC-68）。
            conflict, undecidable = _cross_channel_conflict(
                field_id, rows, state.get("company_profile") or {})
            if conflict:
                state.setdefault("errors", []).append(
                    f"RAG 检出 {field_id} 与既有结构化结论不一致；未自动覆盖，"
                    f"需人工复核（{conflict}）"
                )
            elif undecidable:
                # 比不了就如实说比不了，别伪装成一致，也别伪装成矛盾。
                state.setdefault("cross_channel_undecidable", []).append({
                    "field_id": field_id,
                    "reason": undecidable,
                    "rag_candidates": len(rows),
                })
            continue

        distinct_sources = {row["source"] for row in rows}
        status = "verified"
        conflict_values: List[Dict[str, Any]] = []
        canonical_records: List[Dict[str, Any]] = []
        patch: Optional[Dict[str, Any]] = None

        if field_id in _FINANCIAL_KEYS:
            by_period: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                by_period.setdefault(str(row.get("period")), []).append(row)
            conflict_group = next((group for group in by_period.values()
                                   if len({_financial_comparable(field_id, item).normalize()
                                           for item in group}) > 1
                                   and len({item["source"] for item in group}) > 1), None)
            if conflict_group:
                status = "conflicting"
                conflict_values = [{"source": item["source"],
                                    "value": f"{item['value']} {item.get('unit') or ''}".strip(),
                                    "retrieved_at": retrieved_at} for item in conflict_group]
            else:
                complete, partial_reason = _field_is_complete_enough(field_id, rows)
                if not complete:
                    check["failure_reason"] = partial_reason
                    continue
                key = _FINANCIAL_KEYS[field_id]
                for period, group in sorted(by_period.items()):
                    item = group[0]
                    raw_value = float(item["numeric_value"])
                    if field_id == "debt_ratio":
                        normalized = raw_value / 100.0 if "%" in str(item.get("unit") or item.get("value")) else raw_value
                    else:
                        normalized = _to_wanyuan(raw_value, str(item.get("unit") or "万元"))
                    canonical_records.append({"period": period, key: normalized,
                                              "retrieved_at": retrieved_at})
                patch = {"financials": canonical_records}
        elif field_id in _JUDICIAL_TYPES:
            canonical_records = [row["canonical_record"] for row in rows]
            patch = {"judicial_records": canonical_records}
        elif field_id == "operating_status":
            values = {_compact(row["value"]): row["value"] for row in rows}
            if len(values) > 1 and len(distinct_sources) > 1:
                status = "conflicting"
                conflict_values = [{"source": row["source"], "value": row["value"],
                                    "retrieved_at": retrieved_at} for row in rows]
            else:
                canonical_value = next(iter(values.values()))
                patch = {"registration": {"operating_status": canonical_value}}
        elif field_id in {"guarantee", "bidding_record", "negative_news", "regulatory_penalty"}:
            canonical_records = [row["canonical_record"] for row in rows]
            top_key = {"guarantee": "guarantee", "bidding_record": "bidding_records",
                       "negative_news": "negative_news", "regulatory_penalty": "regulatory_penalty"}[field_id]
            patch = {top_key: canonical_records}
        elif field_id in PROFILE_BACKED_FIELDS:
            # guarantee_circle 需要关系图推导，不能从零散 PDF 句子升级为已核实。
            check["failure_reason"] = "检索到相关文本，但当前文本证据适配器无法确定性投影到评分结构"
            continue

        if status == "verified" and field_id not in _FINANCIAL_KEYS:
            complete, partial_reason = _field_is_complete_enough(field_id, rows)
            if not complete:
                check["failure_reason"] = partial_reason
                continue

        if status == "conflicting":
            value = None
            patch = None
        elif patch is not None:
            try:
                value = _profile_display(field_id, patch, checks)
            except (TypeError, ValueError, KeyError) as exc:
                check["failure_reason"] = f"文本候选未能通过生产字段映射重放：{exc}"
                state.setdefault("errors", []).append(f"RAG 字段 {field_id} 投影失败: {exc}")
                continue
        else:
            values = list(dict.fromkeys(str(row["value"]).strip() for row in rows))
            value = "；".join(values)

        source_dates = [row["publication_date"] for row in rows if row.get("publication_date")]
        raw = {
            "subject_name": state.get("company_name"),
            "field_id": field_id,
            "sources": [{"source": row["source"], "source_id": row["source_id"],
                         "locator": row["locator"], "exact_quote": row["quote"],
                         "publication_date": row["publication_date"],
                         "title": row.get("result", {}).get("title", ""),
                         "url": row.get("result", {}).get("url", "")} for row in rows],
            "canonical_records": canonical_records,
            "canonical_value": value if field_id == "operating_status" else None,
            "conflicting": status == "conflicting",
        }
        try:
            record_structured_evidence(
                store, check, source_adapter=ADAPTER_ID, status=status, value=value,
                conflict_values=conflict_values, profile_patch=patch, raw=raw,
                retrieved_at=retrieved_at,
                as_of_date=max(source_dates) if source_dates else "",
                failure_reason=("多份文本证据对同一字段给出不一致取值，需人工复核"
                                if status == "conflicting" else ""),
            )
            counts[status] += 1
        except (TypeError, ValueError, KeyError) as exc:
            check["failure_reason"] = f"文本候选未能通过结构化证据重放：{exc}"
            state.setdefault("errors", []).append(f"RAG 字段 {field_id} 入库失败: {exc}")

    # 只有最终落为 verified 的字段才能给其原文事实放行。conflicting 和仅部分
    # 命中的字段仍由清单专用上下文披露，不得进入“已核实事实”素材池。
    allowed_quotes = []
    for check in checks:
        if check.get("status") != "verified":
            continue
        for evidence_id in check.get("evidence_ids") or []:
            evidence = store.get(evidence_id) or {}
            for source in (evidence.get("raw") or {}).get("sources") or []:
                allowed_quotes.append((
                    str(source.get("source_id") or ""),
                    str(source.get("locator") or ""),
                    _compact(source.get("exact_quote")),
                ))
    for fact in state.get("facts") or []:
        if not fact.get("rag_evidence_candidate"):
            continue
        metadata = fact.get("metadata") or {}
        fact_quote = _compact(metadata.get("exact_quote"))
        fact["verified"] = any(
            metadata.get("source_id") == source_id
            and metadata.get("locator") == locator
            and fact_quote
            and (fact_quote in evidence_quote or evidence_quote in fact_quote)
            for source_id, locator, evidence_quote in allowed_quotes
        )

    state["completeness"] = compute_completeness(checks)
    state["rag_evidence_summary"] = counts
    return counts
