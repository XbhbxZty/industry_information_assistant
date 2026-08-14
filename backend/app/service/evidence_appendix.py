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
    return f"来源：{src}；取证时间：{ts or '未声明'}"


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
) -> str:
    """
    渲染证据溯源附录。

    只列**主张了事实**的项（verified / conflicting）——unverified 不主张任何事实，
    它属于信息缺口清单而不是证据清单。两者分开列，因为它们要求读者做的事
    完全不同：前者是可追溯的依据，后者是待补的工作。
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
