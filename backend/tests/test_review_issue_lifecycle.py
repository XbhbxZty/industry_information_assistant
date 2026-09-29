"""Deterministic issue contracts, not an evaluation of model error detection."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.analysis_quality import (
    CHECKS, enforce_analysis_review, parse_compact_review, pending_review_issues,
    report_quote_spans, resolve_prior_analysis_issues, upsert_review_issues,
)


REPORT = "材料尚未独立核验，不能据此批准贷款。\n目前仍缺少上期明细，无法完成同比归因。"
QUOTE = REPORT.splitlines()[0]
META = {"finish_reason": "stop"}


def previous(key="issue_old", **extra):
    return {"id": key, "issue_type": "unverified_as_fact", "severity": "critical",
            "location": "旧报告断言", "evidence": "已独立核验", "description": "旧版将未核实材料当事实",
            "suggestion": "纠正事实状态", "resolved": False, "detected_by": "llm", **extra}


def response(prior=None):
    value = {"score": 9, "summary": "完成逐项复核", "issues": [],
             "checks": [{"id": key, "status": "supported", "reason": "报告如实披露限制",
                         "report_quote": QUOTE} for key in CHECKS]}
    if prior:
        value["issue_checks"] = [{"id": item["id"], "status": "resolved", "reason": "报告已明确纠正原事实状态",
                                  "report_quote": QUOTE} for item in prior]
    return value


def parse(raw, prior=None, report=REPORT):
    return parse_compact_review(json.dumps(raw, ensure_ascii=False), META, report, prior_issues=prior)


def validated(raw, prior):
    return enforce_analysis_review(parse(raw, prior), REPORT)


def test_pending_projection_is_untruncated_immutable_and_excludes_protocol():
    feedback = [previous(f"issue_{n}", private_payload="omit") for n in range(20)]
    feedback.extend([previous("closed", resolved=True), previous("protocol", issue_type="review_not_executed"),
                     previous(" "), {"issue_type": "logic_error"}, "bad"])
    before = copy.deepcopy(feedback)
    rows = pending_review_issues(feedback)
    assert len(rows) == 20
    assert all("private_payload" not in row for row in rows)
    rows[0]["description"] = "changed"
    assert feedback == before


def test_duplicate_historical_identity_is_rejected_not_arbitrarily_selected():
    with pytest.raises(ValueError, match="duplicate_prior_issue_id"):
        pending_review_issues([previous(), previous()])


def test_without_history_existing_protocol_remains_unchanged():
    result = parse(response())
    assert "issue_checks" not in result
    assert "reviewed_issue_ids" not in result
    raw = response()
    raw["issue_checks"] = []
    with pytest.raises(ValueError, match="invalid_structure"):
        parse(raw)


@pytest.mark.parametrize("mutation", ["missing", "empty", "duplicate", "unknown", "bad_status", "extra",
                                      "empty_reason", "invented_quote", "empty_quote", "not_array", "bad_row"])
def test_history_protocol_rejects_invalid_or_incomplete_rows(mutation):
    prior = [previous()]
    raw = response(prior)
    row = raw["issue_checks"][0]
    if mutation == "missing":
        raw.pop("issue_checks")
    elif mutation == "empty":
        raw["issue_checks"] = []
    elif mutation == "duplicate":
        raw["issue_checks"].append(copy.deepcopy(row))
    elif mutation == "unknown":
        row["id"] = "unknown"
    elif mutation == "bad_status":
        row["status"] = "supported"
    elif mutation == "extra":
        row["recheck_of"] = prior[0]["id"]
    elif mutation == "empty_reason":
        row["reason"] = " "
    elif mutation == "invented_quote":
        row["report_quote"] = "报告没有这段文本"
    elif mutation == "empty_quote":
        row["report_quote"] = ""
    elif mutation == "not_array":
        raw["issue_checks"] = {}
    else:
        raw["issue_checks"] = [None]
    with pytest.raises(ValueError):
        parse(raw, prior)


def test_rechecks_resolve_only_current_report_anchors():
    prior = [previous()]
    raw = response(prior)
    row = raw["issue_checks"][0]
    row.pop("report_quote")
    row["report_quote_id"] = next(iter(report_quote_spans(REPORT)))
    result = parse(raw, prior)
    assert result["issue_checks"][0]["report_quote"] == QUOTE
    with pytest.raises(ValueError, match="unknown_report_quote_id"):
        parse(raw, prior, REPORT + "\n报告已更新。")
    row["report_quote"] = "不同的文本"
    with pytest.raises(ValueError, match="conflicting_report_quote"):
        parse(raw, prior)


def test_truncated_response_never_yields_closure_receipt():
    prior = [previous()]
    with pytest.raises(ValueError, match="incomplete_response"):
        parse_compact_review(json.dumps(response(prior)), {"finish_reason": "length"}, REPORT, prior_issues=prior)
    assert prior[0]["resolved"] is False


def test_unresolved_result_uses_trusted_original_issue_fields_and_current_location():
    prior = [previous(report_quote_id="old_anchor", requires_new_search=True, search_query="补查主体材料")]
    before = copy.deepcopy(prior)
    raw = response(prior)
    raw["issue_checks"][0].update(status="unresolved", reason="当前答复仍不能解决原问题")
    result = parse(raw, prior)
    issue = result["issues"][0]
    for key in ("id", "issue_type", "severity", "suggestion", "requires_new_search", "search_query"):
        assert issue[key] == prior[0][key]
    assert issue["recheck_of"] == prior[0]["id"]
    assert issue["evidence"] == QUOTE
    assert issue["description"] == raw["issue_checks"][0]["reason"]
    assert "report_quote_id" not in issue
    assert result["overall_assessment"]["verdict"] == "needs_revision"
    assert prior == before


def test_explicit_partial_closure_preserves_other_unresolved_issues():
    prior = [previous("a"), previous("b", issue_type="analysis_quality_error")]
    raw = response(prior)
    raw["issue_checks"][1].update(status="unresolved", reason="原分析错误尚未修正")
    review = validated(raw, prior)
    assert review["overall_assessment"]["verdict"] != "pass"
    resolve_prior_analysis_issues(prior, review)
    assert prior[0]["resolved"] is True
    assert prior[0]["resolution"] == "explicit_issue_recheck"
    assert prior[0]["resolution_quote"] == QUOTE
    assert prior[1]["resolved"] is False


@pytest.mark.parametrize("mutation", ["unvalidated", "degraded", "missing_rows", "missing_ids", "unknown", "omitted",
                                      "duplicate", "bad_reason", "bad_checks", "bad_questions"])
def test_invalid_recheck_never_closes_any_prior_issue(mutation):
    prior = [previous("a"), previous("b")]
    review = validated(response(prior), prior)
    if mutation == "unvalidated":
        review["analysis_review_validated"] = False
    elif mutation == "degraded":
        review["degraded"] = True
    elif mutation == "missing_rows":
        review.pop("issue_checks")
    elif mutation == "missing_ids":
        review.pop("reviewed_issue_ids")
    elif mutation == "unknown":
        review["issue_checks"][0]["id"] = "c"
        review["reviewed_issue_ids"][0] = "c"
    elif mutation == "omitted":
        review["issue_checks"].pop()
        review["reviewed_issue_ids"].pop()
    elif mutation == "duplicate":
        review["issue_checks"][1] = copy.deepcopy(review["issue_checks"][0])
    elif mutation == "bad_reason":
        review["issue_checks"][0]["reason"] = " "
    elif mutation == "bad_checks":
        review["analysis_checks"].pop()
    else:
        review["reviewed_question_ids"] = ["p1"]
    resolve_prior_analysis_issues(prior, review)
    assert not any(issue["resolved"] for issue in prior)


def test_enforcement_checks_history_quote_against_report():
    prior = [previous()]
    parsed = parse(response(prior), prior)
    parsed["issue_checks"][0]["report_quote"] = "伪造定位"
    review = enforce_analysis_review(parsed, REPORT)
    assert review["degraded"]
    assert not review["analysis_review_validated"]
    assert review["issues"][0]["issue_type"] == "review_not_executed"
    resolve_prior_analysis_issues(prior, review)
    assert not prior[0]["resolved"]


def test_global_pass_without_historical_recheck_does_not_close_identified_issues():
    prior = [previous(issue_type="analysis_quality_error")]
    review = enforce_analysis_review(parse(response()), REPORT)
    resolve_prior_analysis_issues(prior, review)
    assert not prior[0]["resolved"]


def test_analysis_and_question_issues_keep_both_identity_and_report_anchor():
    plan = [{"id": "p1"}]
    raw = response()
    anchor = next(iter(report_quote_spans(REPORT)))
    raw["checks"][0].update(status="issue", report_quote_id=anchor)
    raw["question_checks"] = [{"id": "p1", "status": "issue", "reason": "必要答案缺失",
                              "report_quote_id": anchor, "needs_more_evidence": False, "followup_question": ""}]
    parsed = parse_compact_review(json.dumps(raw), META, REPORT, investigation_plan=plan)
    result = enforce_analysis_review(parsed, REPORT, plan)
    assert result["issues"][0]["check_id"] == CHECKS[0]
    assert result["issues"][1]["question_id"] == "p1"
    assert all(issue["report_quote_id"] == anchor for issue in result["issues"])


def test_exact_duplicate_upsert_preserves_identity_and_counts_occurrences():
    ledger = []
    issue = previous()
    before = copy.deepcopy(issue)
    first = upsert_review_issues(ledger, [issue])[0]
    identity = first["id"]
    assert identity != issue["id"]
    second = upsert_review_issues(ledger, [issue])[0]
    assert len(ledger) == 1
    assert second["id"] == identity
    assert second["occurrences"] == 2
    assert issue == before


@pytest.mark.parametrize("field", ["issue_type", "location", "evidence", "description"])
def test_upsert_does_not_merge_merely_similar_issues(field):
    ledger = []
    issue = previous()
    upsert_review_issues(ledger, [issue])
    issue[field] += "不同"
    upsert_review_issues(ledger, [issue])
    assert len(ledger) == 2


def test_explicit_unresolved_upsert_keeps_original_identity_and_initial_assertion():
    ledger = [previous(report_quote_id="old_anchor")]
    raw = response(ledger)
    raw["issue_checks"][0].update(status="unresolved", reason="修改后仍存在原问题")
    result = parse(raw, ledger)
    rows = upsert_review_issues(ledger, result["issues"])
    assert len(ledger) == 1
    assert rows[0]["id"] == "issue_old"
    assert rows[0]["initial_evidence"] == "已独立核验"
    assert rows[0]["description"] == "修改后仍存在原问题"
    assert "report_quote_id" not in rows[0]
    assert rows[0]["resolved"] is False


@pytest.mark.parametrize("identity", ["check_id", "question_id"])
def test_recheck_retains_structured_identity_location_and_counts_once_per_review(identity):
    key = CHECKS[0] if identity == "check_id" else "p1"
    ledger = [previous(issue_type="analysis_quality_error", location=key, **{identity: key})]
    raw = response(ledger)
    raw["issue_checks"][0].update(status="unresolved", reason="当前判断仍不成立")
    result = parse(raw, ledger)
    current = copy.deepcopy(result["issues"][0])
    current.pop("recheck_of")
    assert current["location"] == key
    assert current["evidence"] == QUOTE
    stored = upsert_review_issues(ledger, [result["issues"][0], current])
    assert len(ledger) == len(stored) == 1
    assert stored[0]["id"] == "issue_old"
    assert stored[0]["occurrences"] == 2


def test_upsert_invalid_explicit_reference_is_atomic_and_cannot_reopen_resolved_issue():
    ledger = [previous(resolved=True)]
    before = copy.deepcopy(ledger)
    with pytest.raises(ValueError, match="unknown_recheck_issue"):
        upsert_review_issues(ledger, [previous("new"), previous(recheck_of="issue_old")])
    assert ledger == before


def test_exact_duplicate_of_closed_issue_is_a_new_issue_not_silent_reopening():
    ledger = [previous(resolved=True)]
    stored = upsert_review_issues(ledger, [previous()])
    assert len(ledger) == 2
    assert ledger[0]["resolved"] is True
    assert stored[0]["id"] != ledger[0]["id"]
