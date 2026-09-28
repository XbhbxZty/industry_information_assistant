"""Report categories must not substitute for the operation score's dependencies."""
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from config.dd_checklist import build_field_checks, compute_completeness, is_core_check
from service.risk_scorecard import GATE_CATEGORY_RATE, score


COMPANY = {
    "registration": {"operating_status": "注销"},
    "financials": [{"period": "2025年度", "revenue": 10000.0, "net_profit": 1200.0,
                    "debt_ratio": 0.4, "operating_cash_flow": 900.0}],
    "judicial_records": [], "negative_news": [], "guarantee": [],
    "bidding_records": [{"project": "已核实项目"}],
}


def checks_for(statuses, *, scenario=""):
    checks = build_field_checks(scenario=scenario)
    for check in checks:
        check["status"] = statuses.get(check["field_id"], "verified")
    return checks


def test_missing_optional_bidding_does_not_exclude_verified_registration_status():
    checks = checks_for({"bidding_record": "unverified"})
    completeness = compute_completeness(checks)
    before = deepcopy(completeness)
    result = score(COMPANY, checks, completeness)

    assert completeness == before, "report completeness must remain unchanged"
    assert completeness["by_category"]["operation"]["rate"] == 0.0
    assert next(c for c in checks if c["field_id"] == "operating_status")["category"] == "basic"
    assert result["dimension_scores"]["operation"] == 100.0
    assert "operation" not in result["dimensions_excluded"]
    assert GATE_CATEGORY_RATE not in result["gate_kinds"]
    # The adverse registration rule must actually participate, not just be displayed.
    assert result["composite_score"] == 12.2


@pytest.mark.parametrize("status", ["unverified", "conflicting", "missing_check"])
def test_verified_bidding_never_substitutes_for_registration_status(status):
    checks = checks_for({"operating_status": status})
    if status == "missing_check":
        checks = [c for c in checks if c["field_id"] != "operating_status"]
    completeness = compute_completeness(checks)
    assert completeness["by_category"]["operation"]["rate"] == 1.0

    result = score(COMPANY, checks, completeness)

    assert "operation" in result["dimensions_excluded"]
    assert result["requires_human_review"] is True
    assert result["level"] != "低风险"
    assert GATE_CATEGORY_RATE in result["gate_kinds"]
    assert any("operating_status" in gate for gate in result["gates_applied"])


def test_inapplicable_bidding_and_scenario_fields_do_not_hide_registration_score():
    baseline = checks_for({"bidding_record": "not_applicable"})
    extended = checks_for({"bidding_record": "not_applicable"}, scenario="factoring")
    for check in extended:
        if not is_core_check(check):
            check["status"] = "unverified"
    baseline_result = score(COMPANY, baseline, compute_completeness(baseline))
    extended_completeness = compute_completeness(extended)
    result = score(COMPANY, extended, extended_completeness)

    assert "operation" not in extended_completeness["by_category"]
    assert "operation" not in result["dimensions_excluded"]
    assert result["dimension_scores"]["operation"] == 100.0
    assert result["composite_score"] == baseline_result["composite_score"] == 12.2
    assert result["gate_kinds"] == baseline_result["gate_kinds"]


def test_scenario_only_operation_fields_do_not_create_a_core_dimension_gate():
    checks = checks_for({"operating_status": "not_applicable",
                         "bidding_record": "not_applicable"}, scenario="factoring")
    for check in checks:
        if not is_core_check(check):
            check["status"] = "unverified"
    result = score(COMPANY, checks, compute_completeness(checks))

    assert "operation" not in result["dimension_scores"]
    assert "operation" not in result["dimensions_excluded"]
    assert GATE_CATEGORY_RATE not in result["gate_kinds"]


def test_optional_bidding_gap_does_not_add_an_extra_conflict_escalation_step():
    # EVAL-005's former high-risk label compounded two gates: the erroneous
    # optional-bidding floor and the valid registration-conflict escalation.
    # Removing only the former must preserve the conflict and human review.
    checks = checks_for({"registration": "conflicting", "bidding_record": "unverified"})
    company = {**COMPANY, "registration": {"operating_status": "存续"}}
    result = score(company, checks, compute_completeness(checks))

    assert result["composite_score"] <= 25
    assert result["level"] == "中风险"
    assert result["gate_kinds"] == ["conflict_escalation"]
    assert result["requires_human_review"] is True
    assert next(c for c in checks if c["field_id"] == "registration")["status"] == "conflicting"
