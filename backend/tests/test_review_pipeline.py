"""Programmatic Critic handoff/lifecycle checks, not model quality assertions."""
import asyncio
from copy import deepcopy
import hashlib

import pytest

from test_stage3_review import CriticStub, REPORT, state, wire_review
from service.deep_research_v2.graph import DeepResearchGraph
from service.deep_research_v2.research_outcome import build_research_outcome
from service.deep_research_v2.analysis_quality import REPORT_REVIEW_LIMIT


def content_issue():
    return {"type": "unverified_as_fact", "severity": "major", "quote": REPORT,
            "reason": "这条判断未经独立核实", "fix": "改为条件判断并披露核实限制"}


def recheck(raw, issue_id, status="resolved", quote=REPORT):
    raw["issue_checks"] = [{"id": issue_id, "status": status, "reason": "逐项检查当前报告的判断和限制",
                            "report_quote": quote}]
    return raw


def test_ordinary_history_requires_explicit_current_recheck_before_closing():
    data = state()
    data["iteration"] = 0
    first = wire_review()
    first["issues"] = [content_issue()]
    asyncio.run(CriticStub(first).process(data))
    assert data["phase"] == "revising"
    issue_id = data["critic_feedback"][0]["id"]
    assert data["critic_feedback"][0]["resolved"] is False
    data["phase"] = "reviewing"
    fixed = "该判断只是未经独立核实的企业陈述，不能据此认定已核实。"
    data["final_report"] = fixed
    raw = wire_review()
    for row in raw["checks"] + raw["question_checks"]:
        row["report_quote"] = fixed
    critic = CriticStub(recheck(raw, issue_id, quote=fixed))
    asyncio.run(critic.process(data))
    assert data["critic_feedback"][0]["resolved"] is True
    assert len(data["critic_feedback"]) == 1
    assert data["quality_review"]["verdict"] == "pass"
    assert data["quality_review"]["issue_checks"][0]["id"] == issue_id
    assert data["quality_review"]["review_report_sha256"] == hashlib.sha256(fixed.encode()).hexdigest()
    assert build_research_outcome(data)["outstanding_issue_count"] == 0
    assert issue_id in critic.call_llm.call_args.kwargs["user_prompt"]


def test_missing_history_checks_cannot_turn_new_high_score_into_resolution():
    data = state()
    data["critic_feedback"] = [{"id": "old", "issue_type": "unverified_as_fact", "severity": "major",
                                "evidence": REPORT, "description": "旧问题未解", "resolved": False}]
    critic = CriticStub(wire_review())
    asyncio.run(critic.process(data))
    assert data["critic_feedback"][0]["resolved"] is False
    assert data["quality_review"]["degraded"]
    assert data["quality_review"]["review_failure_kind"] == "protocol"
    assert data["phase"] == "completed"  # No content fix can repair a malformed review.
    assert data["risk_assessment"]["requires_human_review"]
    assert build_research_outcome(data)["report_status"] == "restricted"


def test_repeated_issue_keeps_id_and_last_round_cannot_skip_human_gate():
    data = state()
    data["iteration"] = 0
    issue_id = None
    for round_number in range(3):
        data["phase"] = "reviewing"
        raw = wire_review()
        raw["checks"][0].update(status="issue", reason="缺少必要的有依据结论")
        if issue_id:
            recheck(raw, issue_id, "unresolved")
            raw["issue_checks"][0]["reason"] = raw["checks"][0]["reason"]
        critic = CriticStub(raw)
        asyncio.run(critic.process(data))
        issue_id = data["critic_feedback"][0]["id"]
        if round_number < 2:
            assert data["phase"] == "revising"
            assert DeepResearchGraph._route_after_review(None, data) == "revise"
        else:
            assert data["phase"] == "completed"
            assert DeepResearchGraph._route_after_review(None, data) == "complete"
            assert data["risk_assessment"]["requires_human_review"] is True
            assert any("审核迭代已用尽" in error for error in data["errors"])
    assert all(issue["id"] == issue_id for issue in data["critic_feedback"])
    assert len(data["critic_feedback"]) == 1
    assert build_research_outcome(data)["report_status"] == "restricted"


def test_last_round_followup_does_not_reopen_questions_or_start_unreviewable_work():
    data = state()
    data["iteration"] = 2
    raw = wire_review()
    raw["question_checks"][0].update(status="issue", reason="仍缺底稿", needs_more_evidence=True,
                                     followup_question="用已读引用补算并关联底稿")
    before = deepcopy(data["agent_investigation"])
    asyncio.run(CriticStub(raw).process(data))
    assert data["phase"] == "completed"
    assert "pending_search_queries" not in data
    assert data["agent_investigation"] == before
    assert data["risk_assessment"]["requires_human_review"]


def test_known_report_overflow_is_rejected_before_model_request():
    data = state()
    data["final_report"] = "原" * (REPORT_REVIEW_LIMIT + 1)
    critic = CriticStub(wire_review())
    result = asyncio.run(critic._review_content(data))
    critic.call_llm.assert_not_called()
    assert result["review_failure"] == "review_report_truncated"
    assert result["review_failure_kind"] == "input_limit"


def test_known_citation_overflow_is_rejected_before_model_request():
    data = state()
    quotes = [f"实际已读原文第{i}项。" for i in range(100)]
    notebook = data["agent_investigation"]
    notebook["sources"] = {"s1": {"read": True, "read_texts": ["\n".join(quotes)]}}
    notebook["findings"] = [{"citations": [{"source_id": "s1", "quote": text}
                                          for text in quotes[i:i + 5]]} for i in range(0, 100, 5)]
    critic = CriticStub(wire_review())
    result = asyncio.run(critic._review_content(data))
    critic.call_llm.assert_not_called()
    assert result["review_failure"] == "review_cited_evidence_truncated"


@pytest.mark.parametrize("failure,code", [(asyncio.TimeoutError(), "review_call_timeout"),
                                         (RuntimeError("DO NOT DISCLOSE provider payload"), "review_provider_error:RuntimeError")])
def test_transport_failure_is_persisted_and_not_repeated_as_writer_fix(failure, code):
    data = state()
    critic = CriticStub(wire_review())
    critic.call_llm.side_effect = failure
    asyncio.run(critic.process(data))
    assert data["quality_review"]["review_failure_kind"] == "transport"
    assert data["quality_review"]["review_failure"] == code
    assert "DO NOT DISCLOSE" not in str(data)
    assert data["critic_feedback"][0]["evidence"] == "模型服务请求失败，未获得可用的审核结果"
    assert data["phase"] == "completed"
    assert data["risk_assessment"]["requires_human_review"]
    assert data["iteration"] == 1  # No empty revision, added loop or budget reset.
    critic.call_llm.assert_awaited_once()
    event = next(value for name, value in critic.events if name == "review")
    assert event["review_failure_kind"] == "transport"


@pytest.mark.parametrize("kind,evidence", [
    ("protocol", "审核响应未通过结构或引用校验"),
    ("input_limit", "审核输入超过完整复核上限，本轮未调用模型"),
    (None, "未获得可用审核结果（原因未分类）"),
])
def test_failure_card_distinguishes_non_transport_causes_without_claiming_a_model_review(kind, evidence):
    critic = CriticStub(wire_review())
    result = critic.merge_review(state(), {"review_failure_kind": kind})
    assert result["degraded"] is True
    issue = result["issues"][0]
    assert issue["issue_type"] == "review_not_executed"
    assert issue["evidence"] == evidence
    assert "执行结束不代表质检通过" in issue["suggestion"]


def test_cancellation_is_not_swallowed_as_review_failure():
    critic = CriticStub(wire_review())
    critic.call_llm.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(critic.process(state()))


def test_graph_delivery_notice_does_not_expire_current_review_targets():
    from test_review_writer_targets import state as target_state, issue, revised, WriterStub
    from service.deep_research_v2.investigation_report import plain
    from service.deep_research_v2.review_targets import analysis_revision_issues
    from service.deep_research_v2.research_outcome import strip_outcome_notice
    data = target_state()
    report = data["final_report"]
    quote = plain(data["agent_investigation"]["summary"])
    anchor = issue(data, quote)["report_quote_id"]
    raw = wire_review()
    for row in raw["checks"] + raw["question_checks"]:
        row["report_quote"] = quote
    raw["issues"] = [{"type": "hallucination", "severity": "major", "report_quote_id": anchor,
                      "reason": "不能凭此排除风险", "fix": "撤回无依据结论"}]
    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph.critic = CriticStub(raw)
    graph._enter = lambda *_: True
    graph._cancelled = lambda *_: False
    graph._emit = lambda *_: None
    data = asyncio.run(graph._review_node(data))
    assert "research-outcome:start" in data["final_report"]
    assert strip_outcome_notice(data["final_report"]) == report
    assert graph.critic._content_for_review(data) == report
    assert analysis_revision_issues(data)[0]["repair_target"]["field"] == "summary"
    writer = WriterStub(revised(data))
    asyncio.run(writer._revise_agent_analysis(data))
    writer.call_llm.assert_awaited_once()
    assert all(not row["resolved"] for row in data["critic_feedback"])
    # Genuine report changes, even outside the edited line, still expire IDs.
    data["final_report"] = "新增真实报告断言。\n" + data["final_report"]
    assert not analysis_revision_issues(data)
