"""Delivery restrictions survive sealed checkpoints and financial review.

Use the existing no-model/no-database graph fixture, but real LangGraph
interrupt/resume, production sealing and the human-review completion methods.
"""
import asyncio
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from service.checkpoint_integrity import MODE_STANDARD, verify_graph_state_seal  # noqa: E402
from service.deep_research_v2.analysis_quality import CHECKS  # noqa: E402
from service.deep_research_v2.research_outcome import build_research_outcome  # noqa: E402
from test_graph_equivalence import _build_graph, _DD_QUERY  # noqa: E402


def _restricted_graph():
    graph = _build_graph(requires_review=True)

    async def critic_receipt(state):
        # A passing last round must not clear either historical defects or an
        # execution failure. The separate Critic tests validate real receipts.
        state.update(
            phase="completed", quality_score=10,
            quality_review={"verdict": "pass", "score": 10, "degraded": False,
                            "analysis_checks": [
                                {"id": key, "status": "supported", "reason": "已复核当前分析"}
                                for key in CHECKS]},
            critic_feedback=[{"id": "historic-risk-issue", "issue_type": "unverified_as_fact",
                              "severity": "major", "resolved": False,
                              "description": "历史事实主张尚未解决"}],
            agent_failures=[{"agent": "Scout", "error_type": "TimeoutError"}],
            agent_investigation={"status": "completed"},
        )
        return state

    graph.critic.process = critic_receipt
    return graph


async def _pause(graph, session_id):
    events = [event async for event in graph.run(
        _DD_QUERY, session_id, user_id="u1", due_diligence=True, research_strategy="agent",
    )]
    assert any(event["type"] == "human_review_required" for event in events)
    assert not any(event["type"] == "research_complete" for event in events)
    snapshot = await graph.graph.aget_state({"configurable": {"thread_id": session_id}})
    assert snapshot.next, "the production human-review node must really be interrupted"
    state = copy.deepcopy(snapshot.values)
    verify_graph_state_seal(state, MODE_STANDARD)
    assert "<!-- research-outcome:start -->" in state["final_report"]
    assert "受限报告" in state["final_report"]
    return state


@pytest.mark.parametrize("decision", [
    {"approved": True, "reviewer": "风控测试", "comment": "财务意见通过"},
    {"approved": True, "reviewer": "风控测试", "override_level": "低风险", "comment": "人工调整等级"},
    {"approved": False, "reviewer": "风控测试", "comment": "仍需补充资料"},
], ids=["approve", "override", "reject"])
def test_real_signed_pause_resume_and_human_completion_preserve_delivery_limits(decision):
    async def exercise():
        graph = _restricted_graph()
        session_id = "restricted-real-review"
        paused = await _pause(graph, session_id)
        restored = graph._restore_managed_profile_snapshot(copy.deepcopy(paused), session_id, MODE_STANDARD)
        assert build_research_outcome(restored)["report_status"] == "restricted"

        events = [event async for event in graph.resume_review(session_id, decision, user_id="u1")]
        assert not any(event["type"] == "error" for event in events), events
        complete = next(event for event in events if event["type"] == "research_complete")
        assert complete["research_outcome"]["report_status"] == "restricted"
        assert complete["research_outcome"]["quality_status"] == "needs_revision"
        assert complete["research_outcome"]["execution_status"] == "degraded"
        assert complete["research_outcome"]["outstanding_issue_count"] == 1
        assert "受限报告" in complete["final_report"]
        assert complete["risk_assessment"]["human_review"]["approved"] is decision["approved"]

        final = (await graph.graph.aget_state({"configurable": {"thread_id": session_id}})).values
        verify_graph_state_seal(final, MODE_STANDARD)
        for key in ("quality_review", "agent_failures", "critic_feedback"):
            assert final[key] == paused[key], f"financial review must not rewrite {key}"
        assert final["final_report"].count("<!-- research-outcome:start -->") == 1
        assert graph._build_ui_state(final)["research_outcome"] == complete["research_outcome"]

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["quality_review", "agent_failures", "critic_feedback", "final_report"])
def test_restriction_basis_cannot_be_erased_from_a_signed_paused_state(field):
    async def exercise():
        graph = _restricted_graph()
        session_id = "restricted-tamper-review"
        paused = await _pause(graph, session_id)
        tampered = copy.deepcopy(paused)
        tampered.pop(field)
        with pytest.raises(ValueError, match="完整性"):
            graph._restore_managed_profile_snapshot(tampered, session_id, MODE_STANDARD)

    asyncio.run(exercise())


def test_claimed_review_fallback_preserves_restrictions_and_frozen_basis_without_database():
    async def exercise():
        graph = _restricted_graph()
        session_id = "restricted-claimed-fallback"
        paused = await _pause(graph, session_id)
        original = copy.deepcopy(paused)
        decision = {"approved": True, "reviewer": "风控测试", "override_level": "低风险",
                    "comment": "只调整财务意见，不表示分析问题已经解决"}
        accepted_at = "2026-09-28T12:00:00+00:00"
        candidate = graph._claimed_review_candidate(paused, decision, accepted_at, MODE_STANDARD)

        verify_graph_state_seal(candidate, MODE_STANDARD)
        graph._require_finished_review_match(candidate, paused, decision, accepted_at, MODE_STANDARD)
        assert paused == original, "terminal derivation must not mutate its sealed review basis"
        assert candidate["risk_assessment"]["level"] == "低风险"
        assert build_research_outcome(candidate)["report_status"] == "restricted"
        assert "受限报告" in candidate["final_report"]
        for key in ("quality_review", "agent_failures", "critic_feedback"):
            assert candidate[key] == original[key]

    asyncio.run(exercise())
