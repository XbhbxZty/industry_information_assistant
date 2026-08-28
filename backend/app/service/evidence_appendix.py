# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
证据溯源附录（纯函数，无 LLM）—— v0.7-A

## 为什么需要它

迁移计划里排第一条的业务约束是：

> **报告中每个结论必须可溯源——出坏账要追责。**

v0.6a 花了三轮复核把溯源建起来：来源闭集、受信任适配器注册表、
证据绑定校验、取证时间。但那些信息**至今没有一个字进入报告**。
出了坏账翻开报告，看不到"这条结论是谁在什么时候查到的"（BC-48）。

## 为什么由代码渲染而不是让模型写

与风险评级块同一理由：整合与修订都是 LLM 步骤、都会重写全文。
溯源表若交给模型生成，它会被改写、被精简、被"综合来看"掉——
而一张被模型改过的证据清单比没有更危险，读者会以为它是原始记录。

因此本模块产出**唯一权威版本**，由 `writer._ensure_evidence_appendix()`
在代码层收口，与 `RISK_BLOCK_MARKER` 的处理方式一致。
"""
from typing import Any, Dict, List, Optional

try:
    from config.canonical import format_source_citation
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.config.canonical import format_source_citation

# 附录锚点。与评级块同样的收口机制：模型改写后由代码重建。
APPENDIX_MARKER = "证据溯源附录（由系统生成"
APPENDIX_END = "<!-- /evidence-appendix -->"

# 来源标识 → 人类可读名称。
# 报告读者是风控与信贷评审人员，`relation_registry` 这种内部标识对他们没有意义。
_SOURCE_LABEL = {
    "initial_profile": "初始企业档案",
    "business_registry": "工商登记信息",
    "relation_registry": "关联关系登记库",
    "graph_analysis": "关联关系图谱推导",
    "judicial": "司法公开信息",
    "financial_report": "财务报表",
    "bidding": "招投标公开信息",
    "public_opinion": "公开舆情",
    "rag_text_document_v1": "本地知识库文本文件（逐字校验）",
}


def source_label(adapter_id: Optional[str]) -> str:
    if not adapter_id:
        return "来源未标注"
    return _SOURCE_LABEL.get(adapter_id, adapter_id)


def format_provenance(check: Dict[str, Any]) -> str:
    """
    单条核查项的溯源短句，供撰写提示词内联使用。

    取证时间缺失时**如实写"取证时间未声明"**，不留空也不编造——
    留空会让模型自行补一个日期（BC-48 的成因之一）。
    """
    src = source_label(check.get("source_adapter") or check.get("verification_origin"))
    ts = check.get("retrieved_at") or ""
    evidence_date = check.get("as_of_date") or ""
    return (
        f"来源：{src}；证据日期：{evidence_date or '未确认'}；"
        f"取证时间：{ts or '未声明'}"
    )


def _evidence_note(check: Dict[str, Any], evidence_store: Dict[str, Dict]) -> str:
    """证据编号，供审计时回查原始返回。"""
    ids = check.get("evidence_ids") or []
    found = [e for e in ids if e in (evidence_store or {})]
    if not found:
        return "—"
    return "、".join(found)


def render_appendix(
    field_checks: List[Dict[str, Any]],
    evidence_store: Optional[Dict[str, Dict]] = None,
    completeness: Optional[Dict[str, Any]] = None,
    search_failures: Optional[List[Dict[str, Any]]] = None,
    as_of: str = "",
    section_failures: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    渲染证据溯源附录。

    只列**主张了事实**的项（verified / conflicting）——unverified 不主张任何事实，
    它属于信息缺口清单而不是证据清单。两者分开列，因为它们要求读者做的事
    完全不同：前者是可追溯的依据，后者是待补的工作。

    `search_failures` 与 `as_of` 是 P0 补的两块：
      - 检索故障必须与"查了没有"分开列。案例包的来源目录里 S009/S010
        两条 403 失败单独成行并附免责声明，正是这个道理——不写出来，
        读者无从知道哪些"未发现记录"其实是"没查成"。
      - 截止日必须写明。一份没说"以哪天为准"的尽调报告，事后无法判断
        当时该不该看到某条信息。

    `section_failures` 是 BC-56 补的第三块，与前者同理但粒度更粗：
    检索成功、抽取环节崩溃或超时的章节，其证据同样为空。不单独列出来，
    读者会把"这一章崩了"读成"这一章材料未提供"。
    """
    evidence_store = evidence_store or {}
    checks = list(field_checks or [])
    if not checks:
        return ""

    asserted = [c for c in checks if c.get("status") in ("verified", "conflicting")]
    gaps = [c for c in checks if c.get("status") == "unverified"]

    lines = [
        f"**{APPENDIX_MARKER}，不得由撰写环节改写）**",
        "",
        "本附录记录报告中每一条已核实结论的来源与取证时间，供事后审计与追责。",
        "",
    ]
    if as_of:
        lines += [
            f"**研究截止日**：{as_of}。本报告的全部判断以该日期为准，"
            f"晚于该日发布或发生的信息不参与结论。",
            "",
            "> ⚠️ 时点隔离在结构化数据源与法定披露文件上可严格施加；"
            "通用网页检索的发布日期常缺失，该部分只能尽力过滤，"
            "详见下方「检索时点局限」。",
            "",
        ]
    lines += [
        "| 核查项 | 结论 | 来源 | 取证时间 | 证据编号 |",
        "|---|---|---|---|---|",
    ]
    for c in sorted(asserted, key=lambda x: (x.get("category", ""), x.get("field_id", ""))):
        value = c.get("value")
        if c.get("status") == "conflicting":
            value = "**存在多源冲突**：" + "；".join(
                f"{d.get('source')}={d.get('value')}"
                for d in (c.get("conflict_detail") or [])
            )
        text = str(value or "").replace("|", "／").replace("\n", " ")
        if len(text) > 60:
            text = text[:60] + "…"
        lines.append(
            f"| {c.get('field_name')} | {text} "
            f"| {source_label(c.get('source_adapter') or c.get('verification_origin'))} "
            f"| {c.get('retrieved_at') or '**未声明**'} "
            f"| {_evidence_note(c, evidence_store)} |"
        )

    source_rows = []
    seen_sources = set()
    for check in asserted:
        for evidence_id in check.get("evidence_ids") or []:
            evidence = evidence_store.get(evidence_id) or {}
            for source in (evidence.get("raw") or {}).get("sources") or []:
                key = (source.get("source_id"), source.get("locator"), source.get("source"))
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                source_rows.append(source)
    if source_rows:
        lines += ["", "**原始证据来源**", ""]
        for source in source_rows:
            label = source.get("title") or source.get("source_id") or "未命名来源"
            locator = source.get("locator") or "位置未标注"
            url = source.get("url") or source.get("source") or ""
            # 引用形式由 config.canonical 单点定义：评测器用同一个函数**识别**
            # 它。两侧各写一遍必然漂移，而漂移的表现是评测器把一条真实存在的
            # 引用判成缺失，反过来逼人去改正确的生产代码（BC-59）。
            citation = format_source_citation(source.get("source_id")) or "[—]"
            lines.append(
                f"- {citation} {label}；{locator}；"
                f"发布日期：{source.get('publication_date') or '未确认'}；{url}"
            )

    if gaps:
        lines += [
            "",
            "**尚未核实的项（信息缺口，非「不存在」）**",
            "",
            "| 核查项 | 必查 | 未核实原因 |",
            "|---|---|---|",
        ]
        for c in sorted(gaps, key=lambda x: (not x.get("required"), x.get("field_id", ""))):
            reason = str(c.get("failure_reason") or "数据源未覆盖").replace("|", "／")
            lines.append(
                f"| {c.get('field_name')} | {'是' if c.get('required') else '否'} "
                f"| {reason[:90]} |")
        lines.append("")
        lines.append(
            "> ⚠️ 以上各项**未取得核实结论**，不得解读为「不存在」或「无记录」。"
            "两者在授信判断上的含义完全相反。")

    if completeness:
        rate = completeness.get("verified_rate")
        if isinstance(rate, (int, float)):
            lines += [
                "",
                f"**必查项核实率**：{completeness.get('required_verified', '?')}"
                f"/{completeness.get('required_total', '?')}（{rate:.0%}）",
            ]

    # 取证时间缺失的项单独点名：时效不明的证据不该混在正常记录里被略过
    undated = [c.get("field_name") for c in asserted if not c.get("retrieved_at")]
    if undated:
        lines += [
            "",
            f"> ⚠️ 以下核查项**未声明取证时间**，无法判断证据时效，"
            f"复核时须确认：{('、'.join(undated))}",
        ]

    # 检索故障（P0-2）。**必须与"查了没有"分列**：前者是失败，后者是结论。
    # 把它们混同，一次 API 超时就会以"未发现负面舆情"的形式进入报告。
    if search_failures:
        lines += [
            "",
            "**本次未能完成的检索**",
            "",
            "| 检索源 | 查询 | 失败原因 | 时间 |",
            "|---|---|---|---|",
        ]
        for f in search_failures:
            q = str(f.get("query") or "").replace("|", "／")
            lines.append(
                f"| {source_label(f.get('provider'))} | {q[:40]} "
                f"| {str(f.get('failure_reason') or '')[:40]} "
                f"| {str(f.get('occurred_at') or '')[:19]} |"
            )
        lines += [
            "",
            "> ⚠️ 以上检索**未能完成**，只能证明本次访问失败，"
            "**不得据此认定不存在相关记录**。相关核查项若在上方信息缺口清单中，"
            "须线下补充核查。",
        ]

    # 章节级抽取故障（BC-56）。与检索故障分开成表：检索故障是"没查到"，
    # 这里是"查到了但没读成"——两者要补的工作不同。
    if section_failures:
        lines += [
            "",
            "**本次未能完成的章节抽取**",
            "",
            "| 章节 | 故障类型 | 原因 | 时间 |",
            "|---|---|---|---|",
        ]
        for f in section_failures:
            title = str(f.get("section_title") or f.get("section_id") or "").replace("|", "／")
            kind = "调用超时" if f.get("failure_kind") == "llm_timeout" else "执行异常"
            reason = str(f.get("failure_reason") or "").replace("|", "／")
            lines.append(
                f"| {title[:30]} | {kind} | {reason[:60]} "
                f"| {str(f.get('occurred_at') or '')[:19]} |"
            )
        lines += [
            "",
            "> ⚠️ 以上章节的检索资料**未能完成结构化抽取**。这些章节的证据为空"
            "是故障所致，**不得读作「材料未提供」或「未发现相关记录」**。"
            "本报告在这些章节上的结论不可采信，须重跑或线下补充核查。",
        ]

    if as_of:
        lines += [
            "",
            "**检索时点局限**",
            "",
            "通用网页检索结果的发布日期由检索接口提供，实际常有缺失。"
            "本次已丢弃可确认晚于研究截止日的结果；发布日期无法确认的结果"
            "予以保留并标记，**未能对其施加时点过滤**。"
            "结构化数据源与法定披露文件不受此局限。",
        ]

    lines += ["", APPENDIX_END]
    return "\n".join(lines)


def excise_appendix(text: str) -> str:
    """切除正文中的附录块，保留其余内容。"""
    start = text.find(APPENDIX_MARKER)
    if start < 0:
        return text.strip()
    line_start = text.rfind("\n", 0, start) + 1
    head = text[:line_start].rstrip()
    end = text.find(APPENDIX_END, start)
    tail = text[end + len(APPENDIX_END):].strip() if end >= 0 else ""
    return "\n\n".join(p for p in (head, tail) if p)


def canonicalize_appendix(text: str, block: str) -> str:
    """
    把正文中的附录收敛为一份系统生成的原文。

    与评级块同一策略：模型复制/改写/截断过的版本一律切除，放回权威版本。
    附录固定置于报告末尾——它是审计材料，不参与阅读流。
    """
    if not block:
        return text
    body = excise_appendix(text)
    while APPENDIX_MARKER in body:
        stripped = excise_appendix(body)
        if stripped == body:
            break
        body = stripped
    return "\n\n".join(p for p in (body.rstrip(), "---", block) if p)
