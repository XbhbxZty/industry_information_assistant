"""Protocol regression tests; these do not measure a live model's error recall."""
import copy
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.analysis_quality import CHECKS, enforce_analysis_review, apply_analysis_revision, parse_compact_review
from service.deep_research_v2.agents.critic import CriticMaster
from service.review_verdict import derive_verdict, unresolved_blocking_issues


def receipt(quote="报告"):
    return {"overall_assessment": {"quality_score": 9, "verdict": "pass"}, "issues": [],
            "analysis_checks": [{"id": key, "status": "supported", "reason": "已对照来源与口径", "report_quote": quote}
                                for key in CHECKS]}


class CriticStub(CriticMaster):
    def __init__(self):
        self.logger = logging.getLogger("analysis-test")


def test_missing_review_blocks_agent_dd_but_not_legacy():
    raw = {"overall_assessment": {"quality_score": 9, "verdict": "pass"}, "issues": []}
    state = {"research_strategy": "agent", "due_diligence_mode": True, "final_report": "报告"}
    result = CriticStub().merge_review(state, raw)
    assert result["degraded"]
    assert result["overall_assessment"]["verdict"] == "major_issues"
    assert len(result["issues"]) == 4
    assert raw["issues"] == []
    state["research_strategy"] = "classic"
    assert CriticStub().merge_review(state, copy.deepcopy(raw))["overall_assessment"]["verdict"] == "pass"


@pytest.mark.parametrize("key,quote,reason", [
    ("cashflow_attribution", "应收增加240占现金流同比下降300的80%，因此是主因。",
     "本期调节额240并非两期调节额之差，缺少上期调节表，不能解释同比300。"),
    ("receipt_reconciliation", "期末应收已收回90。",
     "长账龄90加短账龄40=期末应收回款130，另有预收70，总额200；不能把90代替130。"),
])
def test_identified_errors_cannot_be_passed_or_lost_at_iteration_limit(key, quote, reason):
    raw = receipt(quote)
    row = next(c for c in raw["analysis_checks"] if c["id"] == key)
    row.update(status="issue", reason=reason, report_quote=quote)
    result = enforce_analysis_review(raw, quote)
    assert raw["issues"] == []
    assert result["issues"][0]["issue_type"] == "analysis_quality_error"
    result["issues"][0]["severity"] = "minor"
    assert derive_verdict(result["issues"], 9, "pass")["verdict"] == "major_issues"
    assert unresolved_blocking_issues(result["issues"])


@pytest.mark.parametrize("mutation", ["absent", "duplicate", "bad_status", "empty_reason", "invented_quote"])
def test_malformed_receipts_are_incomplete_review_not_financial_error(mutation):
    raw = receipt()
    rows = raw["analysis_checks"]
    if mutation == "absent":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0].copy())
    elif mutation == "bad_status":
        rows[0]["status"] = "pass"
    elif mutation == "empty_reason":
        rows[0]["reason"] = " "
    else:
        rows[0].update(status="issue", report_quote="不存在的片段")
    result = enforce_analysis_review(raw, "实际报告")
    assert result["degraded"]
    assert all(i["issue_type"] == "review_not_executed" for i in result["issues"])


def test_cautious_correct_report_and_reasoned_not_applicable_can_pass():
    raw = receipt()
    raw["analysis_checks"][2]["reason"] = "仅有本期调节表，报告明确不能完成同比归因，没有声称贡献率。"
    raw["analysis_checks"][3].update(status="not_applicable", reason="问题和报告只涉及工商主体识别，不涉及回款。")
    result = enforce_analysis_review(raw, "报告")
    assert not result["issues"]
    assert derive_verdict(result["issues"], 9, "pass")["verdict"] == "pass"


def notebook():
    return {"status": "stalled", "summary": "原概述", "sources": {"s1": {"read": True}},
            "actions": [{"action": "read_source"}],
            "findings": [{"claim": "原判断需要复核", "quote": "长账龄回款90，短账龄回款40。",
                          "source_id": "s1", "verified": False, "citation_status": "located"}]}


def revision():
    return {"summary": "期末应收回款130，其中长账龄90；不是全部只有90。",
            "findings": [{"index": 0, "claim": "长短账龄合计回款130，均待独立核验。",
                          "quote": "伪造引文", "source_id": "evil", "verified": True}],
            "missing_materials": ["银行流水用于独立核验"]}


def test_revision_preserves_evidence_actions_status_and_history():
    data = notebook()
    before = copy.deepcopy(data)
    apply_analysis_revision(data, revision())
    for key in ("status", "sources", "actions"):
        assert data[key] == before[key]
    for key in ("quote", "source_id", "verified", "citation_status"):
        assert data["findings"][0][key] == before["findings"][0][key]
    assert data["findings"][0]["inference_status"] == "revision_pending_review"
    assert data["analysis_revisions"][0]["summary"] == "原概述"


@pytest.mark.parametrize("bad", [None, {}, {"summary": "摘要", "findings": [], "missing_materials": []},
    {"summary": "摘要", "findings": [{"index": True, "claim": "这不是合法的索引"}], "missing_materials": []}])
def test_revision_is_atomic_on_invalid_output(bad):
    data = notebook()
    before = copy.deepcopy(data)
    with pytest.raises(ValueError):
        apply_analysis_revision(data, bad)
    assert data == before


def test_writer_revision_updates_appendix_not_rule_state():
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from service.deep_research_v2.agents.writer import LeadWriter

    class WriterStub(LeadWriter):
        def __init__(self):
            self.name = "writer-test"
            self.logger = logging.getLogger("writer-test")
            self.call_llm = AsyncMock(return_value=json.dumps(revision(), ensure_ascii=False))
        def add_message(self, *args):
            pass
        def _rerender_due_diligence_report(self, state):
            from service.deep_research_v2.investigation_report import append_investigation_report
            state["final_report"] = append_investigation_report("规则正文", state["agent_investigation"])
            return state

    state = {"due_diligence_mode": True, "research_strategy": "agent", "agent_investigation": notebook(),
             "query": "回款多少", "field_checks": [{"status": "unverified"}],
             "risk_assessment": {"requires_human_review": True},
             "critic_feedback": [{"issue_type": "analysis_quality_error", "resolved": False}]}
    before = copy.deepcopy(state)
    writer = WriterStub()
    asyncio.run(writer._revise_report(state))
    assert "130" in state["final_report"]
    for key in ("field_checks", "risk_assessment", "critic_feedback"):
        assert state[key] == before[key]
    writer.call_llm.assert_awaited_once()
    assert "30至200字" in writer.call_llm.call_args.kwargs["system_prompt"]
    invalid = revision()
    invalid["findings"][0]["claim"] = "过长" * 400
    writer.call_llm.return_value = json.dumps(invalid)
    preserved = copy.deepcopy(state["agent_investigation"])
    asyncio.run(writer._revise_report(state))
    assert state["agent_investigation"] == preserved
    assert "修订发现长度无效" in state["errors"][-1]
    writer.call_llm.side_effect = RuntimeError("provider failure")
    preserved = copy.deepcopy(state["agent_investigation"])
    asyncio.run(writer._revise_report(state))
    assert state["agent_investigation"] == preserved
    assert "RuntimeError" in state["errors"][-1]


def test_critic_receives_agent_tail_beyond_ordinary_report_window():
    import asyncio
    import json
    from unittest.mock import AsyncMock
    critic = CriticStub()
    raw = {"score": 9, "summary": "复核完成", "issues": [], "checks": receipt()["analysis_checks"]}
    critic.call_llm = AsyncMock(return_value=(json.dumps(raw), {"finish_reason": "stop"}))
    critic._cfg_temperature = lambda: 0.2
    critic._cfg_max_tokens = lambda: 4000
    state = {"research_strategy": "agent", "due_diligence_mode": True,
             "query": "检查分析", "facts": [], "data_points": [], "outline": [],
             "field_checks": [], "final_report": "前文" * 5000 + "尾部需要复核",
             "agent_investigation": {"summary": "尾部需要复核", "findings": []}}
    asyncio.run(critic._review_content(state))
    prompt = critic.call_llm.call_args.kwargs["user_prompt"]
    assert "尾部需要复核" in prompt
    system = critic.call_llm.call_args.kwargs["system_prompt"]
    assert "checks" in system
    assert "cashflow_attribution" in system
    assert critic.call_llm.call_args.kwargs["return_meta"] is True
    parsed_context = json.loads(prompt)
    assert parsed_context["claims_to_audit"]["summary"] == "尾部需要复核"


@pytest.mark.parametrize("finish", ["length", "content_filter", "", "tool_calls"])
def test_truncated_review_rejected_even_if_json_is_complete(finish):
    import json
    raw = {"score": 9, "summary": "通过", "issues": [], "checks": receipt()["analysis_checks"]}
    with pytest.raises(ValueError, match="incomplete_response"):
        parse_compact_review(json.dumps(raw), {"finish_reason": finish}, "报告")


@pytest.mark.parametrize("mutation", ["mixed_issues", "extra_key", "duplicate_check", "nonfinite_score", "unlocated_quote"])
def test_compact_protocol_rejects_structural_degradation(mutation):
    import json
    raw = {"score": 9, "summary": "通过", "issues": [], "checks": receipt()["analysis_checks"]}
    if mutation == "mixed_issues":
        raw["issues"] = ["description"]
    elif mutation == "extra_key":
        raw["description"] = "错层字段"
    elif mutation == "duplicate_check":
        raw["checks"][0] = raw["checks"][1]
    elif mutation == "nonfinite_score":
        raw["score"] = float("nan")
    else:
        raw["issues"] = [{"type": "logic_error", "severity": "major", "quote": "编造原文", "reason": "错误", "fix": "修订"}]
    with pytest.raises(ValueError):
        parse_compact_review(json.dumps(raw), {"finish_reason": "stop"}, "报告")


def test_duplicate_json_key_not_silently_overwritten():
    with pytest.raises(ValueError, match="duplicate_key"):
        parse_compact_review('{"score":3,"score":9}', {"finish_reason": "stop"}, "报告")


def test_review_failure_metadata_survives_fail_closed_merge():
    raw = {"review_failure": "review_incomplete_response", "call_meta": {"finish_reason": "length"}}
    result = CriticStub().merge_review({"research_strategy": "agent", "due_diligence_mode": True}, raw)
    assert result["review_failure"] == "review_incomplete_response"
    assert result["call_meta"]["finish_reason"] == "length"
    assert result["overall_assessment"]["verdict"] == "major_issues"


def test_only_complete_passing_recheck_resolves_previous_analysis_issues():
    from service.deep_research_v2.analysis_quality import resolve_prior_analysis_issues
    prior = [{"issue_type": "analysis_quality_error", "resolved": False},
             {"issue_type": "review_not_executed", "resolved": False},
             {"issue_type": "unverified_as_fact", "resolved": False}]
    review = receipt()
    review["degraded"] = True
    resolve_prior_analysis_issues(prior, review)
    assert not any(i["resolved"] for i in prior)
    review.pop("degraded")
    saved = review.pop("analysis_checks")
    resolve_prior_analysis_issues(prior, review)
    assert not any(i["resolved"] for i in prior)
    review["analysis_checks"] = saved
    review = enforce_analysis_review(review, "报告")
    resolve_prior_analysis_issues(prior, review)
    assert [i["resolved"] for i in prior] == [True, True, False]
