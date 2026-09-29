"""An ended run, a good rating and a high review score are different outcomes."""
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.research_outcome import (
    build_research_outcome, append_outcome_notice,
)
from service.deep_research_v2.analysis_quality import CHECKS
from service.risk_scorecard import INSUFFICIENT


def clean_state():
    return {
        "phase": "completed", "due_diligence_mode": True, "research_strategy": "agent",
        "agent_investigation": {"status": "completed"}, "final_report": "# 调查报告\n原文",
        "quality_score": 10, "critic_feedback": [],
        "quality_review": {"verdict": "pass", "score": 10, "analysis_checks": [
            {"id": key, "status": "supported", "reason": "已对照证据", "report_quote": "原文"} for key in CHECKS]},
        "risk_assessment": {"level": "低风险", "requires_human_review": False,
                            "credit_recommendation": {"recommendable": True}},
    }


@pytest.mark.parametrize("issue", [
    {"severity": "critical", "issue_type": "new_issue_type"},
    {"severity": "major", "issue_type": "logic_error"},
    {"severity": "minor", "issue_type": "post_cutoff_evidence"},
])
def test_historical_issue_survives_last_round_ten_and_good_rating(issue):
    state = clean_state()
    state["critic_feedback"] = [{**issue, "resolved": False}]
    original = copy.deepcopy(state)
    result = build_research_outcome(state)
    assert result["execution_status"] == "finished"
    assert result["quality_status"] == "needs_revision"
    assert result["report_status"] == "restricted"
    assert result["outstanding_issue_count"] == 1
    assert state == original, "delivery status must never rewrite financial policy"
    state["critic_feedback"][0]["resolved"] = True
    assert build_research_outcome(state)["report_status"] == "ready"


@pytest.mark.parametrize("status", ["stalled", "time_limit", "step_limit", "running"])
def test_unfinished_investigation_is_restricted_even_without_review_issues(status):
    state = clean_state()
    state["agent_investigation"]["status"] = status
    result = build_research_outcome(state)
    assert result["quality_status"] == "passed"
    assert result["report_status"] == "restricted"
    assert result["investigation_status"] == status


def test_missing_audit_is_not_a_pass_and_human_approval_cannot_clear_it():
    state = clean_state()
    state["quality_review"]["analysis_checks"] = []
    state["risk_assessment"]["human_review"] = {"approved": True}
    result = build_research_outcome(state)
    assert result["quality_status"] == "not_reviewed"
    assert result["report_status"] == "restricted"


@pytest.mark.parametrize("mutation", ["bad_id", "duplicate", "empty_reason", "bad_status"])
def test_invalid_saved_audit_cannot_be_displayed_as_passed(mutation):
    state = clean_state()
    checks = state["quality_review"]["analysis_checks"]
    if mutation == "bad_id":
        checks[0]["id"] = ["question_coverage"]
    elif mutation == "duplicate":
        checks[0] = checks[1].copy()
    elif mutation == "empty_reason":
        checks[0]["reason"] = " "
    else:
        checks[0]["status"] = "pass"
    outcome = build_research_outcome(state)
    assert outcome["quality_status"] == "not_reviewed"
    assert outcome["report_status"] == "restricted"


def test_insufficient_material_is_partial_not_execution_failure():
    state = clean_state()
    state["risk_assessment"] = {"level": INSUFFICIENT, "requires_human_review": True}
    state["completeness"] = {"required_total": 15, "required_verified": 1}
    result = build_research_outcome(state, "awaiting_review")
    assert result["execution_status"] == "awaiting_review"
    assert result["report_status"] == "partial"
    assert result["rating_status"] == "insufficient"
    assert result["credit_status"] == "unavailable"
    assert not result["restriction_reasons"]


def test_node_exception_and_material_gap_are_not_conflated():
    state = clean_state()
    state["agent_failures"] = [{"agent": "Scout", "error_type": "RuntimeError"}]
    assert build_research_outcome(state)["report_status"] == "restricted"
    assert build_research_outcome(state)["execution_status"] == "degraded"
    state.pop("agent_failures")
    assert build_research_outcome(state)["report_status"] == "ready"


def test_caught_evaluation_failure_is_not_presented_as_a_material_gap(monkeypatch):
    from service import risk_evaluation
    from service.deep_research_v2.agents.data_analyst import DataAnalyst

    def fail(*args):
        raise ValueError("test-only evaluation failure")

    monkeypatch.setattr(risk_evaluation, "compute_completeness", fail)
    state = clean_state()
    state.update(field_checks=[{"field_id": "revenue"}], messages=[], errors=[])
    DataAnalyst("test-key", "http://localhost:1", "test-model").assess_risk(state)
    assert state["risk_evaluation_status"] == {"status": "error", "stage": "completeness"}
    outcome = build_research_outcome(state)
    assert outcome["rating_status"] == "insufficient"
    assert outcome["execution_status"] == "degraded"
    assert outcome["report_status"] == "restricted"


def test_notice_is_idempotent_and_clears_only_when_conditions_clear():
    state = clean_state()
    state["agent_investigation"]["status"] = "stalled"
    outcome = build_research_outcome(state)
    report = append_outcome_notice(state["final_report"], outcome)
    assert "受限报告" in report and "stalled" in report
    assert append_outcome_notice(report, outcome) == report
    state["agent_investigation"]["status"] = "completed"
    assert append_outcome_notice(report, build_research_outcome(state)) == state["final_report"]


def test_archived_qilan_failure_cannot_be_projected_as_a_ready_report():
    path = Path(__file__).resolve().parents[1] / "eval/agent_e2e_pack/runs/QL-approve-agent-001"
    state = json.loads((path / "result.json").read_text(encoding="utf-8"))
    state["agent_investigation"] = json.loads((path / "investigation.json").read_text(encoding="utf-8"))
    state["final_report"] = (path / "report.md").read_text(encoding="utf-8")
    before = copy.deepcopy(state["risk_assessment"])
    result = build_research_outcome(state)
    assert result["execution_status"] == "finished"
    assert result["quality_status"] == "needs_revision"
    assert result["report_status"] == "restricted"
    assert result["outstanding_issue_count"] > 0
    assert state["risk_assessment"] == before


def test_archived_t01_high_score_without_audit_receipt_is_not_quality_approval():
    path = Path(__file__).resolve().parents[1] / "eval/agent_e2e_pack/runs/T01-agent-003"
    state = json.loads((path / "checkpoint-summary.json").read_text(encoding="utf-8"))
    state["agent_investigation"] = json.loads((path / "investigation.json").read_text(encoding="utf-8"))
    state["final_report"] = (path / "report.md").read_text(encoding="utf-8")
    before = copy.deepcopy(state["risk_assessment"])
    outcome = build_research_outcome(state, "awaiting_review")
    assert outcome["execution_status"] == "awaiting_review"
    assert outcome["investigation_status"] == "stalled"
    assert outcome["quality_status"] == "not_reviewed"
    assert outcome["report_status"] == "restricted"
    assert outcome["rating_status"] == "insufficient"
    assert state["risk_assessment"] == before


def test_graph_projects_same_outcome_and_persists_notice_before_delivery():
    from service.deep_research_v2.graph import DeepResearchGraph, build_complete_event
    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph.critic = SimpleNamespace(name="critic")
    graph._enter = lambda *a: True
    events = []
    graph._emit = events.append
    async def run_agent(agent, state):
        state["phase"] = "completed"
        return True
    graph._run_agent = run_agent
    state = clean_state()
    state["agent_investigation"] = {"status": "stalled", "sources": {"private": "raw"}}
    state = asyncio.run(graph._review_node(state))
    assert "受限报告" in state["final_report"]
    complete = build_complete_event(state, [])
    ui = graph._build_ui_state(state)
    assert complete["research_outcome"] == ui["research_outcome"]
    assert events[-1]["type"] == "report_draft"
    assert events[-1]["content"]["content"] == state["final_report"]
    assert "sources" not in ui["agent_investigation"]
    paused = graph._review_request(state, state["risk_assessment"])
    assert paused["research_outcome"]["execution_status"] == "awaiting_review"


@pytest.mark.parametrize("historical_issue", [False, True])
def test_real_critic_process_saves_receipt_and_preserves_historical_blockers(historical_issue):
    from service.deep_research_v2.agents.critic import CriticMaster
    from service.deep_research_v2.graph import DeepResearchGraph, build_complete_event

    class StubCritic(CriticMaster):
        def __init__(self):
            self.name = "test-critic"
            self.logger = logging.getLogger(self.name)

        async def _review_content(self, state):
            return {
                "overall_assessment": {"verdict": "pass", "quality_score": 10},
                "issues": [], "analysis_checks": copy.deepcopy(clean_state()["quality_review"]["analysis_checks"]),
            }

        def add_message(self, *args):
            pass

    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph.critic = StubCritic()
    graph._enter = lambda *a: True
    graph._cancelled = lambda *a: False
    events = []
    graph._emit = events.append
    state = clean_state()
    state.pop("quality_review")
    state.update(phase="writing", iteration=1, max_iterations=2)
    # A prior analysis issue can be closed by the existing complete recheck,
    # but unrelated unresolved fact/subject/cutoff issues must survive.
    state["critic_feedback"] = [{"issue_type": "analysis_quality_error", "severity": "major", "resolved": False}]
    if historical_issue:
        state["critic_feedback"].append({"issue_type": "post_cutoff_evidence", "severity": "critical", "resolved": False})
    original_assessment = copy.deepcopy(state["risk_assessment"])
    state = asyncio.run(graph._review_node(state))
    assert state["phase"] == "completed"
    assert state["quality_review"]["verdict"] == ("major_issues" if historical_issue else "pass")
    assert state["quality_review"]["score"] == 10
    assert len(state["quality_review"]["analysis_checks"]) == 4
    assert state["critic_feedback"][0]["resolved"] is True
    assert state["unresolved_issues"] == int(historical_issue)
    expected_status = "restricted" if historical_issue else "ready"
    complete = build_complete_event(state, [])
    assert complete["research_outcome"]["report_status"] == expected_status
    assert events[-1]["content"]["research_outcome"] == complete["research_outcome"]
    assert ("受限报告" in state["final_report"]) is historical_issue
    assert "报告草稿：" not in state["final_report"]
    if historical_issue:
        assert state["risk_assessment"]["requires_human_review"] is True
        assert any("审核迭代已用尽" in gate for gate in state["risk_assessment"]["gates_applied"])
        for key, value in original_assessment.items():
            if key not in ("requires_human_review", "gates_applied"):
                assert state["risk_assessment"][key] == value
    else:
        assert state["risk_assessment"] == original_assessment


def test_real_agent_exception_reaches_restricted_notice_and_degraded_event():
    from service.deep_research_v2.graph import DeepResearchGraph, build_complete_event

    async def fail(state):
        raise RuntimeError("test-only critic failure")

    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph.critic = SimpleNamespace(name="test-failing-critic", process=fail)
    graph._enter = lambda *a: True
    graph._cancelled = lambda *a: False
    graph._emit = lambda *a: None
    state = clean_state()
    state.pop("quality_review")
    state = asyncio.run(graph._review_node(state))
    assert state["agent_failures"] == [{"agent": "test-failing-critic", "error_type": "RuntimeError"}]
    assert "受限报告" in state["final_report"]
    outcome = build_complete_event(state, [])["research_outcome"]
    assert outcome["execution_status"] == "degraded"
    assert outcome["report_status"] == "restricted"
    assert outcome["quality_status"] != "passed"


def test_cancellation_projects_cancelled_and_prevents_next_graph_node():
    from langgraph.graph import END
    from service.deep_research_v2.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph._cancelled = lambda *a: True
    events = []
    graph._emit = events.append
    state = asyncio.run(graph._visualize_node(clean_state()))
    assert state["_cancelled"] is True
    assert graph._guard("write")(state) == END
    assert events[0]["type"] == "research_cancelled"
    assert events[0]["research_outcome"]["execution_status"] == "cancelled"
    graph._mark_cancelled(state)
    assert len(events) == 1
