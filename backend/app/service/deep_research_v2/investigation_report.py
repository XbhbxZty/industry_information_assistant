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
    for finding in notebook.get("findings", [])[:20]:
        lines.extend([f"- [{plain(finding.get('kind'))}] {plain(finding.get('claim'))}",
                      f"  - 依据：{plain(finding.get('quote'))}",
                      f"  - 来源：{plain(finding.get('title'))}；{plain(finding.get('url'))}；{plain(finding.get('source_id'))}"])
    if not notebook.get("findings"):
        lines.append("本次未形成带原文引用的调查发现。")
    # Summary is model interpretation, never inserted into the rating block.
    if notebook.get("summary"):
        lines.extend(["", "### 调查员概述（AI 分析）", "", plain(notebook["summary"])])
    missing = notebook.get("missing_materials") or notebook.get("questions") or []
    if missing:
        lines.extend(["", "### 待解决问题与补件", ""])
        lines.extend(f"- {plain(item)}" for item in missing[:8])
    lines.append(END)
    return report + "\n\n" + "\n".join(lines)
