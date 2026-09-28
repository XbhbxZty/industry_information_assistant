"""Render the agent's cited analysis separately from deterministic verdicts."""
import html
import re

START = "<!-- agent-investigation:start -->"
END = "<!-- agent-investigation:end -->"


def plain(value):
    # Source/model text is data, not Markdown structure, HTML or executable links.
    text = html.escape(str(value or ""), quote=False).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{\}\[\]()#!|>])", r"\\\1", text)


def append_investigation_report(report, notebook):
    report = re.sub(re.escape(START) + r".*?" + re.escape(END), "", report, flags=re.S).rstrip()
    lines = [START, "## 自主调查记录", "",
             "以下为 AI 基于材料提出的分析与待核实线索，不替代上文规则评级。引文存在不等于分析结论已获独立核实。", "",
             f"调查状态：{plain(notebook.get('status', 'not_started'))}", ""]
    quoted = set()
    for finding in notebook.get("findings", [])[:20]:
        lines.append(f"- [{plain(finding.get('kind'))}] {plain(finding.get('claim'))}")
        for citation in (finding.get("citations") or [finding])[:6]:
            key = (citation.get("source_id"), citation.get("quote"))
            if key not in quoted:
                lines.append(f"  - 依据：{plain(citation.get('quote'))}")
                quoted.add(key)
            lines.append(f"  - 来源：{plain(citation.get('title'))}；{plain(citation.get('source_id'))}/{plain(citation.get('quote_id'))}")
            if citation.get("url"):
                lines.append(f"    - 地址：{plain(citation['url'])}")
        if finding.get("calculation_ids"):
            lines.append("  - 计算底稿：" + plain("、".join(finding["calculation_ids"])))
    if not notebook.get("findings"):
        lines.append("本次未形成带原文引用的调查发现。")
    if notebook.get("calculations"):
        lines.extend(["", "### 可复算的分析底稿（不等于已核实事实）", ""])
        for calculation in notebook["calculations"][:12]:
            lines.append(f"- {plain(calculation.get('id'))} {plain(calculation.get('label'))}：{plain(calculation.get('expression'))} = {plain(calculation.get('result'))} {plain(calculation.get('result_unit'))}")
            for name, variable in calculation.get("variables", {}).items():
                lines.append(f"  - {plain(name)}={plain(variable.get('value'))} {plain(variable.get('unit'))}；期间：{plain(variable.get('period'))}；主体：{plain(variable.get('subject') or '未注明')}；{plain(variable.get('source_id'))}/{plain(variable.get('quote_id'))}")
                key = (variable.get("source_id"), variable.get("quote"))
                if key not in quoted:
                    lines.append(f"    - 原文：{plain(variable.get('quote'))}")
                    quoted.add(key)
            lines.append("  - 限制：" + plain(calculation.get("limitations")))
    # Summary is model interpretation, never inserted into the rating block.
    if notebook.get("summary"):
        lines.extend(["", "### 调查员概述（AI 分析）", "", plain(notebook["summary"])])
    missing = notebook.get("missing_materials") or notebook.get("questions") or []
    if missing:
        lines.extend(["", "### 待解决问题与补件", ""])
        lines.extend(f"- {plain(item)}" for item in missing[:8])
    lines.append(END)
    return report + "\n\n" + "\n".join(lines)
