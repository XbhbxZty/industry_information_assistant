"""Delivery semantics, independent of credit scores and model self-ratings.

These projections describe the existing policy; they do not introduce a new
rating-readiness policy or resolve review issues on behalf of a reviewer.
"""
from __future__ import annotations

import math
import re

from ..review_verdict import DD_BLOCKING_ISSUE_TYPES, MIN_PASS_SCORE
from ..risk_scorecard import INSUFFICIENT
from .analysis_quality import CHECKS


def outstanding_issues(state):
    return [i for i in state.get("critic_feedback", []) if isinstance(i, dict)
            and i.get("resolved") is not True
            and (i.get("issue_type") in DD_BLOCKING_ISSUE_TYPES
                 or i.get("severity") in ("critical", "major"))]


def build_research_outcome(state, execution_status=None):
    """Public, bounded projection; never publish raw evidence or tool receipts."""
    assessment = state.get("risk_assessment") or {}
    notebook = state.get("agent_investigation") or {}
    agent_mode = state.get("research_strategy") == "agent"
    investigation = notebook.get("status", "not_started") if agent_mode else "not_applicable"
    if investigation not in ("not_started", "not_applicable", "running", "completed", "stalled", "time_limit", "step_limit"):
        investigation = "unknown"
    issues = outstanding_issues(state)
    review = state.get("quality_review") or {}
    rows = review.get("analysis_checks") or []
    audit_complete = isinstance(rows, list) and len(rows) == len(CHECKS) and all(
        isinstance(row, dict) and isinstance(row.get("id"), str)
        and row.get("status") in ("supported", "issue", "not_applicable")
        and isinstance(row.get("reason"), str) and bool(row["reason"].strip()) for row in rows
    ) and {row.get("id") for row in rows} == set(CHECKS)
    plan = notebook.get("investigation_plan") or []
    question_checks = review.get("question_checks") or []
    if plan:
        audit_complete = audit_complete and isinstance(question_checks, list) and len(question_checks) == len(plan) and all(
            isinstance(row, dict) and isinstance(row.get("id"), str)
            and row.get("status") in ("supported", "issue")
            and isinstance(row.get("reason"), str) and bool(row["reason"].strip())
            and isinstance(row.get("report_quote"), str) and bool(row["report_quote"].strip())
            and row["report_quote"] in (state.get("final_report") or "") for row in question_checks
        ) and {row.get("id") for row in question_checks} == {q.get("id") for q in plan}
    score = review.get("score")
    valid_score = type(score) in (int, float) and math.isfinite(score)
    if issues or (review and (review.get("verdict") != "pass" or not valid_score
                             or score < MIN_PASS_SCORE)):
        quality = "needs_revision"
    elif review.get("degraded") or (agent_mode and not audit_complete):
        quality = "not_reviewed"
    elif agent_mode and (any(row["status"] == "issue" for row in rows) or (plan and any(
            row["status"] == "issue" for row in question_checks))):
        quality = "needs_revision"
    elif review:
        quality = "passed"
    else:
        quality = "not_reviewed"
    reasons = []
    from .question_ledger import coverage
    question_status = coverage(notebook)
    if agent_mode and notebook.get("plan_required") and not question_status["total"]:
        reasons.append("尚未建立核心问题清单，不能确认用户问题已被覆盖。")
    if agent_mode and question_status["open"]:
        reasons.append(f"仍有 {question_status['open']} 个核心问题尚未调查完成，不能以流程结束替代问题回答。")
    if issues:
        reasons.append(f"仍有 {len(issues)} 项重大或阻断级复核问题未解决，末轮高分不能覆盖历史问题。")
    if quality == "needs_revision" and not issues:
        reasons.append("最近一次质量复核未通过。")
    if agent_mode and investigation != "completed":
        reasons.append(f"自主调查未正常完成（{investigation}），不得将未读或未核实视为材料不存在。")
    if (agent_mode or review) and quality == "not_reviewed":
        reasons.append("未保存有效的质量复核结果，不能宣称报告已通过质检。")
    if state.get("agent_failures"):
        reasons.append("至少一个执行节点失败；已有报告仅作为受限产物交付。")
    evaluation_failed = (state.get("risk_evaluation_status") or {}).get("status") == "error"
    if evaluation_failed:
        reasons.append("风险评估执行异常，不能把执行故障解释为资料不足。")

    execution = execution_status or (
        "cancelled" if state.get("_cancelled") else
        "finished" if state.get("phase") == "completed" else "running")
    if execution == "finished" and (state.get("agent_failures") or evaluation_failed):
        execution = "degraded"
    comp = state.get("completeness") or {}
    incomplete = bool(comp.get("required_total")) and (
        comp.get("required_verified", 0) < comp["required_total"])
    rating = ("not_evaluated" if not assessment else
              "insufficient" if assessment.get("level") == INSUFFICIENT else "available")
    has_report = bool(state.get("final_report"))
    report_status = ("unavailable" if not has_report else "restricted" if reasons else
                     "partial" if incomplete or rating == "insufficient" or question_status["blocked"] or notebook.get("partial") else
                     "draft" if quality == "not_reviewed" or execution != "finished" else "ready")
    recommendation = assessment.get("credit_recommendation") or {}
    return {
        "version": 1, "execution_status": execution,
        "investigation_status": investigation, "quality_status": quality,
        "report_status": report_status, "rating_status": rating,
        "credit_status": "available" if recommendation.get("recommendable") is True else "unavailable",
        "requires_human_review": assessment.get("requires_human_review") is True,
        "outstanding_issue_count": len(issues), "restriction_reasons": reasons,
    }


_START = "<!-- research-outcome:start -->"
_END = "<!-- research-outcome:end -->"


def append_outcome_notice(report, outcome):
    """Render only code-owned status text, never model-provided HTML."""
    report = re.sub(re.escape(_START) + r".*?" + re.escape(_END), "", report, flags=re.S).rstrip()
    if not report:
        return report
    status = outcome["report_status"]
    if status not in ("restricted", "partial", "draft"):
        return report
    title = {"restricted": "受限报告：调查或质检仍有未决问题",
             "partial": "部分材料报告：未核实事项仍需补充",
             "draft": "报告草稿：交付流程或质量复核尚未完成"}[status]
    reasons = outcome["restriction_reasons"]
    return report + "\n\n" + _START + "\n\n## 本轮交付范围\n\n" + title + "。\n\n" + (
        "\n".join("- " + reason for reason in reasons) + "\n\n" if reasons else ""
    ) + "流程结束、规则评级和人工意见均不自动证明调查完整或质检问题已解决。\n\n" + _END
