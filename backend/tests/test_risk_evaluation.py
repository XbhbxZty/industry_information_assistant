"""Production and fast evaluation must share evidence-to-decision semantics."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "app"))

from config.dd_checklist import build_field_checks  # noqa: E402
from service import risk_evaluation as evaluation_module  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402
from service.datasource import apply_all  # noqa: E402
from service.deep_research_v2.agents.data_analyst import DataAnalyst  # noqa: E402
from service.deep_research_v2.state import ResearchState, create_initial_state  # noqa: E402
from service.risk_evaluation import evaluate_risk  # noqa: E402
from service.risk_scorecard import INSUFFICIENT  # noqa: E402
from service.verification import (  # noqa: E402
    record_structured_evidence, register_adapter, unregister_adapter,
)

COMPANIES = json.loads((BACKEND / "app/data/companies_eval.json").read_text(encoding="utf-8"))["companies"]
TRUTH = json.loads((BACKEND / "eval/ground_truth.json").read_text(encoding="utf-8"))["cases"]
_spec = importlib.util.spec_from_file_location("risk_test_run_fast", BACKEND / "eval/run_fast.py")
run_fast = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_fast)


def _prepare(company):
    checks = build_field_checks(checked_at="2026-08-10T00:00:00")
    fill_field_checks(company, profile_to_facts(company), checks)
    store = {}
    apply_all(company, checks, store)
    return checks, store


def _production(company, checks, store, *, as_of=""):
    state = {
        "company_profile": copy.deepcopy(company), "field_checks": copy.deepcopy(checks),
        "evidence_store": copy.deepcopy(store), "as_of": as_of,
        "messages": [], "errors": [], "scoring_view": {"stale": True},
        "completeness": {"verified_rate": 123},
    }
    result = DataAnalyst("test-key", "http://localhost:1", "test-model").assess_risk(state)
    return result, state


def _run_with_prepared_evidence(monkeypatch, company, checks, store):
    # Feed identical evidence into the fast runner's preparation seam. The
    # assessment itself remains real, so an unconditional bare score regresses.
    monkeypatch.setattr(run_fast, "build_field_checks", lambda **kwargs: copy.deepcopy(checks))
    monkeypatch.setattr(run_fast, "profile_to_facts", lambda profile: [])
    monkeypatch.setattr(run_fast, "fill_field_checks", lambda *args: None)
    monkeypatch.setattr(run_fast, "apply_all", lambda profile, fields, evidence: evidence.update(copy.deepcopy(store)))
    return run_fast.run_case(company, {"case_id": "same-input", "scenario": "entrypoint parity"})


@pytest.mark.parametrize("company", COMPANIES, ids=lambda c: c["company_id"])
def test_existing_fixtures_have_identical_complete_assessment_in_all_entrypoints(monkeypatch, company):
    company = copy.deepcopy(company)
    prepared = []

    def capture_inputs(profile, checks, store):
        prepared.append(copy.deepcopy((checks, store)))
        return evaluate_risk(profile, checks, store)

    # Use the runner's real preparation, preserving generated fact/evidence IDs
    # so the *entire* assessment (not just its level) is comparable.
    monkeypatch.setattr(run_fast, "evaluate_risk", capture_inputs)
    expected = next(row for row in TRUTH if row["case_id"] == company["company_id"])
    fast = run_fast.run_case(company, expected)
    checks, store = prepared[0]
    before = copy.deepcopy((company, checks, store))
    evaluation = evaluate_risk(company, checks, store)
    production, state = _production(company, checks, store)

    assert evaluation.assessment == production == fast["assessment"]
    assert state["completeness"] == evaluation.completeness
    assert fast["evaluation_diagnostics"] == evaluation.diagnostics
    assert fast["evaluation_errors"] == state["errors"] == evaluation.errors
    assert state["scoring_view"] == evaluation.scoring_view
    assert (company, checks, store) == before, "the pure evaluator must not modify evidence"
    event = next(row["content"] for row in state["messages"] if row["type"] == "risk_assessment")
    assert event["credit_recommendation"] == production["credit_recommendation"]
    assert event["gates_applied"] == production["gates_applied"]


@pytest.mark.parametrize("kind", ["missing_profile", "mismatch", "missing_origin", "invalid_as_of", "future_cutoff"])
def test_failed_preconditions_fail_closed_and_discard_previous_scoring_view(monkeypatch, kind):
    company = copy.deepcopy(COMPANIES[0])
    checks, store = _prepare(company)
    as_of = ""
    if kind == "missing_profile":
        company = {}
    elif kind == "mismatch":
        next(c for c in checks if c["field_id"] == "revenue")["value"] = "forged value"
    elif kind == "missing_origin":
        next(c for c in checks if c["field_id"] == "revenue").pop("verification_origin")
    elif kind == "invalid_as_of":
        as_of = "not-a-date"
    else:
        as_of = "2020-01-01"

    evaluation = evaluate_risk(company, checks, store, as_of=as_of)
    production, state = _production(company, checks, store, as_of=as_of)
    assert evaluation.assessment == production
    assert production["level"] == INSUFFICIENT
    assert production["credit_recommendation"]["recommendable"] is False
    assert evaluation.scoring_view is None and state["scoring_view"] == {}
    assert not evaluation.diagnostics["chain_ok"]
    assert evaluation.errors
    if not as_of:
        fast = _run_with_prepared_evidence(monkeypatch, company, checks, store)
        assert fast["assessment"] == production
        assert fast["chain_ok"] is False


def test_unmergeable_real_adapter_evidence_never_exports_a_partial_scoring_view(monkeypatch):
    company = copy.deepcopy(COMPANIES[0])
    checks, store = _prepare(company)
    guarantee = next(c for c in checks if c["field_id"] == "guarantee")
    # A valid, registered source can still lack the projection required to
    # merge its data into the scorecard. Do not mock this rejection branch.
    adapter = "risk_evaluation_test_no_projection"
    register_adapter(adapter, "test adapter without a scoring projection")
    try:
        guarantee.update(status="unverified", value=None, evidence_ids=[])
        record_structured_evidence(
            store, guarantee, source_adapter=adapter, status="verified",
            value="对外担保 1800 万元", raw={"amount": 1800},
            retrieved_at="2026-08-10T00:00:00",
        )
        evaluation = evaluate_risk(company, checks, store)
        production, state = _production(company, checks, store)
        fast = _run_with_prepared_evidence(monkeypatch, company, checks, store)
        assert evaluation.diagnostics["mismatches"] == []
        assert evaluation.diagnostics["unmergeable"]
        assert evaluation.diagnostics["stage"] == "scoring_view"
        assert evaluation.assessment == production == fast["assessment"]
        assert production["level"] == INSUFFICIENT
        assert production["credit_recommendation"]["recommendable"] is False
        assert evaluation.scoring_view is None and state["scoring_view"] == {}
        assert not fast["chain_ok"]
    finally:
        unregister_adapter(adapter)


def test_source_degradation_gates_precede_credit_recommendation_in_all_entrypoints(monkeypatch):
    company = copy.deepcopy(COMPANIES[0])
    company["coverage"].pop("retrieved_at", None)
    company["registration"].pop("retrieved_at", None)
    checks, store = _prepare(company)
    evaluation = evaluate_risk(company, checks, store)
    production, state = _production(company, checks, store)
    fast = _run_with_prepared_evidence(monkeypatch, company, checks, store)
    assert evaluation.diagnostics["degradations"]
    assert evaluation.diagnostics["chain_ok"]
    assert production["level"] not in ("低风险", INSUFFICIENT)
    assert production["credit_recommendation"]["based_on_level"] == production["level"]
    assert evaluation.assessment == production == fast["assessment"]
    event = next(row["content"] for row in state["messages"] if row["type"] == "risk_assessment")
    assert event["provenance_degradations"] == production["provenance_degradations"]


@pytest.mark.parametrize("stage", ["scoring", "credit_advice"])
def test_runtime_failures_are_auditable_and_cannot_reuse_old_scoring_inputs(monkeypatch, stage):
    company = copy.deepcopy(COMPANIES[0])
    checks, store = _prepare(company)

    def fail(*args, **kwargs):
        raise ValueError("test-only failure")

    monkeypatch.setattr(evaluation_module, "score_risk" if stage == "scoring" else "recommend_credit", fail)
    evaluation = evaluate_risk(company, checks, store)
    production, state = _production(company, checks, store)
    fast = _run_with_prepared_evidence(monkeypatch, company, checks, store)
    assert evaluation.diagnostics["status"] == "error"
    assert evaluation.diagnostics["stage"] == stage
    assert evaluation.diagnostics["exception"]["type"] == "ValueError"
    assert evaluation.assessment == production == fast["assessment"]
    assert production["level"] == INSUFFICIENT
    assert evaluation.scoring_view is None and state["scoring_view"] == {}
    assert state["errors"] == evaluation.errors
    assert fast["evaluation_ok"] is False


def test_no_checklist_does_not_fabricate_a_due_diligence_assessment():
    evaluation = evaluate_risk({}, [])
    production, state = _production({}, [], {})
    assert evaluation.assessment is production is None
    assert evaluation.diagnostics["status"] == "skipped"
    assert not state["messages"]
    assert state["scoring_view"] == {}


def test_failed_reassessment_clears_old_view_in_real_langgraph_merged_checkpoint():
    company = copy.deepcopy(COMPANIES[0])
    checks, store = _prepare(company)
    analyst = DataAnalyst("test-key", "http://localhost:1", "test-model")

    def previous_run(state):
        return {
            "scoring_view": {"stale": True},
            "company_profile": {}, "field_checks": checks, "evidence_store": store,
        }

    def reassess(state):
        assert state["scoring_view"] == {"stale": True}
        analyst.assess_risk(state)
        return state

    builder = StateGraph(ResearchState)
    builder.add_node("previous_run", previous_run)
    builder.add_node("reassess", reassess)
    builder.set_entry_point("previous_run")
    builder.add_edge("previous_run", "reassess")
    builder.add_edge("reassess", END)
    graph = builder.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "clear-stale-risk-view"}}
    result = graph.invoke(create_initial_state("test", "clear-stale-risk-view"), config)

    assert result["risk_assessment"]["level"] == INSUFFICIENT
    assert result["scoring_view"] == {}
    assert graph.get_state(config).values["scoring_view"] == {}
    assert any(snapshot.values.get("scoring_view") == {"stale": True}
               for snapshot in graph.get_state_history(config)), "the prior view was really checkpointed"
