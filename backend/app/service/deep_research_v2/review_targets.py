"""Locate editable AI analysis in the actual delivered report, without judging it."""
from copy import deepcopy
import re

from .analysis_quality import report_quote_spans
from .investigation_report import START, END, plain
from .research_outcome import strip_outcome_notice


_ORDINARY_ISSUES = frozenset({
    "unverified_as_fact", "conflict_silently_resolved", "unsupported_risk_conclusion",
    "subject_attribution_error", "post_cutoff_evidence", "missing_source", "logic_error",
    "bias", "hallucination", "outdated", "incomplete",
})


def _editable_lines(report, notebook):
    # Multiple/malformed appendices cannot grant the writer editing authority.
    if report.count(START) != 1 or report.count(END) != 1:
        return {}
    lower, upper = report.index(START) + len(START), report.index(END)
    if lower >= upper:
        return {}
    lines = []
    for match in re.finditer(r"[^\r\n]+", report):
        raw = match.group()
        quote = raw.strip()
        start = match.start() + len(raw) - len(raw.lstrip())
        end = start + len(quote)
        if quote and lower <= start < end <= upper:
            lines.append({"start": start, "end": end, "quote": quote, "raw": raw})
    editable = {}

    def section(heading):
        matches = [row for row in lines if row["quote"] == heading]
        if len(matches) != 1:
            return None
        start = matches[0]["end"]
        end = next((row["start"] for row in lines
                    if row["start"] > start and row["raw"].startswith("### ")), upper)
        return start, end

    def add(text, field, bounds, **metadata):
        if not bounds:
            return
        matches = [row for row in lines if bounds[0] <= row["start"] < row["end"] <= bounds[1]
                   and row["quote"] == text.strip()]
        if len(matches) != 1:
            return
        row = matches[0]
        key = row["start"], row["end"]
        target = {"field": field, "report_quote": row["quote"],
                  "start": row["start"], "end": row["end"], **metadata}
        # Two editable fields with the same location are ambiguous, not aliases.
        editable[key] = None if key in editable else target

    if notebook.get("summary"):
        add(plain(notebook["summary"]), "summary", section("### 调查员概述（AI 分析）"))
    if notebook.get("missing_materials"):
        bounds = section("### 待解决问题与补件")
        for index, item in enumerate(notebook["missing_materials"][:8]):
            add("- " + plain(item), f"missing_materials[{index}]", bounds)

    # Findings precede frozen calculations and the summary/supplement sections.
    analysis_end = next((row["start"] for row in lines if row["quote"] in (
        "### 可复算的分析底稿（不等于已核实事实）", "### 调查员概述（AI 分析）", "### 待解决问题与补件")), upper)
    for index, finding in enumerate(notebook.get("findings", [])[:20]):
        add(f"- [{plain(finding.get('kind'))}] {plain(finding.get('claim'))}",
            f"findings[{index}].claim", (lower, analysis_end))

    bounds = section("### 核心问题与完成情况（AI 分析，不代表独立核实）")
    labels = {"answered": "已回答，待复核", "blocked": "资料不足，暂不能完整回答", "open": "尚未调查完成"}
    for index, question in enumerate(notebook.get("investigation_plan", [])[:6]):
        if not bounds:
            break
        heading = (f"- {plain(question.get('id'))} [{labels.get(question.get('status'), '状态无法确认')}] "
                   f"{plain(question.get('question'))}")
        matches = [row for row in lines if bounds[0] <= row["start"] < bounds[1] and row["quote"] == heading]
        if len(matches) != 1:
            continue
        start = matches[0]["end"]
        end = next((row["start"] for row in lines if start < row["start"] < bounds[1]
                    and row["raw"].startswith("- ")), bounds[1])
        add("- 当前答复：" + plain(question.get("answer") or "本轮尚未形成答复"),
            f"investigation_plan[{index}].answer", (start, end), question_id=question.get("id"))
        if question.get("limitations"):
            add("- 限制与缺口：" + plain(question["limitations"]),
                f"investigation_plan[{index}].limitations", (start, end), question_id=question.get("id"))
    return {key: value for key, value in editable.items() if value is not None}


def analysis_revision_issues(state):
    """Select unresolved editable issues; keep legacy analysis-check compatibility.

    Selection is only an editing target, never proof that an issue is correct or
    resolved. Source quotes, calculation workpapers and rule-owned text stay frozen.
    """
    report = strip_outcome_notice(state.get("final_report") or "")
    notebook = state.get("agent_investigation") or {}
    editable = _editable_lines(report, notebook)
    spans = report_quote_spans(report)
    selected = []
    for issue in state.get("critic_feedback", []):
        if not isinstance(issue, dict) or issue.get("resolved"):
            continue
        if issue.get("issue_type") == "analysis_quality_error":
            selected.append(deepcopy(issue))
            continue
        if issue.get("issue_type") not in _ORDINARY_ISSUES:
            continue
        anchor = issue.get("report_quote_id")
        if anchor is not None:
            span = spans.get(anchor) if isinstance(anchor, str) else None
            if not span:
                continue
            evidence = issue.get("evidence")
            if evidence is not None and evidence != span["quote"]:
                continue
            target = editable.get((span["start"], span["end"]))
        else:
            evidence = issue.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                continue
            # Critic permits short continuous quotes. They must occur exactly
            # once across the entire report and stay inside one editable line.
            # Lookahead also detects overlapping repeats (e.g. AA within AAA).
            occurrences = [match.start() for match in re.finditer(
                "(?=" + re.escape(evidence) + ")", report)]
            if len(occurrences) != 1:
                continue
            start = occurrences[0] + len(evidence) - len(evidence.lstrip(" \t"))
            end = occurrences[0] + len(evidence.rstrip(" \t"))
            targets = [row for row in editable.values() if row["start"] <= start < end <= row["end"]]
            target = targets[0] if len(targets) == 1 else None
        if target:
            selected.append({**deepcopy(issue), "repair_target": deepcopy(target)})
    return selected
