"""Bounded question review/revision contracts; no live-model recall claims."""
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.analysis_quality import (
    CHECKS, COMPACT_REVIEW_PROMPT, apply_analysis_revision,
    enforce_analysis_review, parse_compact_review, resolve_prior_analysis_issues,
)
from service.deep_research_v2.agents.critic import CriticMaster
from service.deep_research_v2.agents.writer import LeadWriter
from service.deep_research_v2.state import ResearchPhase


REPORT = "经营现金流勾稽尚未完成，现有记录不足；不能声称已经核实或完成归因。"


def plan():
    return [{"id": "p1", "question": "经营现金流是否完成勾稽？", "done_when": "给出同口径调节项相加结果并披露限制",
             "calculation_required": True, "status": "answered", "answer": "原答案仍需复核", "limitations": "未经独立核实",
             "citations": [{"source_id": "s1", "quote_id": "q1", "quote": "本年净利润100万元，折旧20万元。"}],
             "calculation_ids": ["c1"]}]


def wire_review(*, with_plan=True):
    value = {"score": 9, "summary": "已逐项对照报告和原文", "issues": [], "checks": [
        {"id": key, "status": "supported", "reason": "报告明确披露了当前可回答范围和限制", "report_quote": REPORT}
        for key in CHECKS]}
    if with_plan:
        value["question_checks"] = [{"id": "p1", "status": "supported", "reason": "报告没有把未完成勾稽说成已完成",
                                     "report_quote": REPORT, "needs_more_evidence": False, "followup_question": ""}]
    return value


def parse(value, report=REPORT, questions=None):
    return parse_compact_review(json.dumps(value, ensure_ascii=False), {"finish_reason": "stop"},
                                report, plan() if questions is None and "question_checks" in value else questions)


class CriticStub(CriticMaster):
    def __init__(self, value):
        self.logger = logging.getLogger("stage3-critic")
        self.name = "critic-test"
        self.call_llm = AsyncMock(return_value=(json.dumps(value, ensure_ascii=False), {"finish_reason": "stop"}))
        self.events = []

    def add_message(self, state, event, payload):
        self.events.append((event, copy.deepcopy(payload)))

    def _cfg_temperature(self):
        return 0.0

    def _cfg_max_tokens(self):
        return 4000


def state():
    return {"phase": ResearchPhase.REVIEWING.value, "research_strategy": "agent", "due_diligence_mode": True,
            "query": "解释现金流，并核对回款分类", "final_report": REPORT,
            "agent_investigation": {"status": "completed", "investigation_plan": plan(), "findings": [],
                                     "summary": REPORT, "calculations": [{"id": "c1", "result": "120"}]},
            "field_checks": [], "critic_feedback": [], "errors": [], "iteration": 1, "max_iterations": 3,
            "risk_assessment": {"level": "低风险", "requires_human_review": False},
            "facts": [], "data_points": [], "outline": []}


@pytest.mark.parametrize("status", ["supported", "issue", "not_applicable"])
def test_every_nonempty_quote_must_be_in_report_even_for_passing_or_inapplicable_checks(status):
    raw = wire_review(with_plan=False)
    raw["checks"][0].update(status=status, report_quote="只出现在原始来源、不在报告的分类合计")
    with pytest.raises(ValueError, match="unlocated_quote"):
        parse(raw)
    merged = enforce_analysis_review({"issues": [], "analysis_checks": raw["checks"]}, REPORT)
    assert merged["degraded"] and merged["analysis_review_validated"] is False
    assert {row["issue_type"] for row in merged["issues"]} == {"review_not_executed"}


@pytest.mark.parametrize("status", ["supported", "issue"])
@pytest.mark.parametrize("quote", ["", None, "   "])
def test_support_and_issue_need_nonempty_report_anchors(status, quote):
    raw = wire_review(with_plan=False)
    raw["checks"][0].update(status=status, report_quote=quote)
    with pytest.raises(ValueError):
        parse(raw)


def test_genuine_not_applicable_may_omit_quote_but_must_explain_why():
    raw = wire_review(with_plan=False)
    raw["checks"][3].update(status="not_applicable", reason="本次问题及报告均不涉及收款分类", report_quote="")
    reviewed = enforce_analysis_review(parse(raw), REPORT)
    assert reviewed["analysis_review_validated"] and not reviewed["issues"]


@pytest.mark.parametrize("mutation", ["absent", "duplicate", "foreign", "source_quote", "no_anchor", "missing_followup", "false_with_followup"])
def test_each_question_requires_one_valid_audit_receipt(mutation):
    raw = wire_review()
    if mutation == "absent":
        raw.pop("question_checks")
    elif mutation == "duplicate":
        raw["question_checks"].append(copy.deepcopy(raw["question_checks"][0]))
    elif mutation == "foreign":
        raw["question_checks"][0]["id"] = "p999"
    elif mutation == "source_quote":
        raw["question_checks"][0]["report_quote"] = plan()[0]["citations"][0]["quote"]
    elif mutation == "no_anchor":
        raw["question_checks"][0]["report_quote"] = ""
    elif mutation == "missing_followup":
        raw["question_checks"][0].update(status="issue", needs_more_evidence=True)
    else:
        raw["question_checks"][0]["followup_question"] = "无授权的新行动"
    with pytest.raises(ValueError):
        parse(raw, questions=plan())


def test_legacy_without_question_plan_keeps_four_check_protocol():
    raw = wire_review(with_plan=False)
    result = enforce_analysis_review(parse(raw), REPORT)
    assert result["analysis_review_validated"] and result["reviewed_question_ids"] == []
    assert result["question_checks"] == []
    assert not result["issues"]


def test_valid_question_problem_routes_for_focused_reading_and_reopens_only_same_id():
    raw = wire_review()
    raw["question_checks"][0].update(status="issue", reason="报告未完成所问勾稽，材料目录中的明细尚未读取",
                                     needs_more_evidence=True, followup_question="阅读已登记现金流调节表并核对本期勾稽")
    critic = CriticStub(raw)
    data = state()
    prior_question = copy.deepcopy(data["agent_investigation"]["investigation_plan"][0])
    before_risk = copy.deepcopy(data["risk_assessment"])
    asyncio.run(critic.process(data))
    assert data["phase"] == ResearchPhase.RE_RESEARCHING.value
    assert data["pending_search_queries"] == ["阅读已登记现金流调节表并核对本期勾稽"]
    question = data["agent_investigation"]["investigation_plan"][0]
    assert question["status"] == "open" and "复核补查" in question["limitations"]
    for key in ("id", "question", "done_when", "answer", "citations", "calculation_ids"):
        assert question[key] == prior_question[key]
    assert data["risk_assessment"] == before_risk
    assert data["quality_review"]["question_checks"] == raw["question_checks"]
    event = next(payload for kind, payload in critic.events if kind == "review")
    assert event["question_checks"] == raw["question_checks"] and event["analysis_review_validated"]
    prompt = critic.call_llm.call_args.kwargs
    assert "question_checks" in prompt["system_prompt"]
    assert json.loads(prompt["user_prompt"])["claims_to_audit"]["investigation_plan"][0]["id"] == "p1"


def test_wording_problem_routes_to_writer_not_new_research():
    raw = wire_review()
    raw["question_checks"][0].update(status="issue", reason="现有材料足够，仅需把错误声称完成改成明确限制")
    data = state()
    critic = CriticStub(raw)
    asyncio.run(critic.process(data))
    assert data["phase"] == ResearchPhase.REVISING.value and "pending_search_queries" not in data
    assert data["critic_feedback"][0]["issue_type"] == "analysis_quality_error"


def test_missing_calculation_workpaper_routes_back_to_tools_not_frozen_writer():
    raw = wire_review()
    raw["question_checks"][0].update(status="issue", reason="相关数字已经读到，但尚未产生可复算的现金流勾稽底稿",
        needs_more_evidence=True, followup_question="使用已读利润及各调节项引用调用calculate，逐项相加后回填p1")
    data = state()
    critic = CriticStub(raw)
    asyncio.run(critic.process(data))
    assert data["phase"] == ResearchPhase.RE_RESEARCHING.value
    assert "调用calculate" in data["pending_search_queries"][0]
    assert data["agent_investigation"]["investigation_plan"][0]["status"] == "open"
    assert "需要新增/修正计算底稿" in critic.call_llm.call_args.kwargs["system_prompt"]


def test_protocol_failure_is_unreviewed_not_a_financial_error_or_research_instruction():
    raw = wire_review()
    raw["checks"][2]["report_quote"] = "来源中的现金流数字，不在当前报告"
    critic = CriticStub(raw)
    data = state()
    asyncio.run(critic.process(data))
    assert data["phase"] == ResearchPhase.REVISING.value
    assert data["quality_review"]["degraded"] and not data["quality_review"]["analysis_review_validated"]
    assert {item["issue_type"] for item in data["critic_feedback"]} == {"review_not_executed"}
    assert data["risk_assessment"]["level"] == "低风险"


def test_history_requires_complete_question_recheck_and_server_validated_receipt():
    previous = [{"issue_type": "analysis_quality_error", "resolved": False},
                {"issue_type": "review_not_executed", "resolved": False}]
    raw = wire_review()
    missing = parse(raw)
    resolve_prior_analysis_issues(previous, missing)
    assert not any(item["resolved"] for item in previous)
    raw["question_checks"][0].update(status="issue", reason="问题尚未得到准确回答")
    failed = enforce_analysis_review(parse(raw), REPORT, plan())
    # A provider's passing label cannot hide per-question issues.
    failed["overall_assessment"]["verdict"] = "pass"
    resolve_prior_analysis_issues(previous, failed)
    assert not any(item["resolved"] for item in previous)
    valid = enforce_analysis_review(parse(wire_review()), REPORT, plan())
    resolve_prior_analysis_issues(previous, valid)
    assert all(item["resolved"] for item in previous)


def revision_notebook():
    return {"status": "completed", "summary": "原摘要", "findings": [], "investigation_plan": plan(),
            "calculations": [{"id": "c1", "expression": "profit + depreciation", "result": "120",
                              "variables": {"profit": {"value": "100", "quote": "本期净利润100万元。"}}}],
            "sources": {"s1": {"read": True}}, "actions": [{"action": "calculate"}]}


def revision():
    return {"summary": "现有材料尚不足以完成勾稽，不能把未完成事项称为已完成。", "findings": [], "missing_materials": [],
            "investigation_plan": [{"id": "p1", "status": "blocked", "answer": "尚缺其他调节项目",
                                    "limitations": "需要补齐完整调节明细才可重新回答"}]}


def test_writer_may_correct_answer_or_downgrade_status_but_keeps_all_evidence_and_history():
    data = revision_notebook()
    before = copy.deepcopy(data)
    apply_analysis_revision(data, revision())
    changed = data["investigation_plan"][0]
    assert changed["status"] == "blocked" and changed["answer"] == "尚缺其他调节项目"
    for key in ("id", "question", "done_when", "calculation_required", "citations", "calculation_ids"):
        assert changed[key] == before["investigation_plan"][0][key]
    for key in ("calculations", "sources", "actions", "status"):
        assert data[key] == before[key]
    assert data["analysis_revisions"][-1]["investigation_plan"] == before["investigation_plan"]


@pytest.mark.parametrize("mutation", ["missing_plan", "new_question", "rewrite_question", "invent_citation", "rewrite_calculation",
                                      "upgrade_status", "blocked_without_limit", "answered_without_citation", "answered_without_calculation"])
def test_invalid_question_revision_is_atomic(mutation):
    data, update = revision_notebook(), revision()
    row = update["investigation_plan"][0]
    if mutation == "missing_plan":
        update.pop("investigation_plan")
    elif mutation == "new_question":
        row["id"] = "p999"
    elif mutation == "rewrite_question":
        row["question"] = "把原问题替换成简单问题"
    elif mutation == "invent_citation":
        row["citations"] = [{"source_id": "invented"}]
    elif mutation == "rewrite_calculation":
        row["calculation_ids"] = ["invented"]
    elif mutation == "upgrade_status":
        data["investigation_plan"][0]["status"] = "open"
        row["status"] = "answered"
    elif mutation == "blocked_without_limit":
        row["limitations"] = ""
    elif mutation == "answered_without_citation":
        data["investigation_plan"][0]["citations"] = []
        row["status"] = "answered"
    else:
        data["investigation_plan"][0]["calculation_ids"] = []
        row["status"] = "answered"
    before = copy.deepcopy(data)
    with pytest.raises(ValueError):
        apply_analysis_revision(data, update)
    assert data == before


def test_writer_llm_receives_plan_and_only_commits_bounded_answer_revision():
    class WriterStub(LeadWriter):
        def __init__(self):
            self.name = "stage3-writer"
            self.logger = logging.getLogger("stage3-writer")
            self.call_llm = AsyncMock(return_value=json.dumps(revision(), ensure_ascii=False))
        def add_message(self, *_):
            pass
    data = state()
    data["agent_investigation"] = revision_notebook()
    data["critic_feedback"] = [{"issue_type": "analysis_quality_error", "resolved": False}]
    before = copy.deepcopy(data)
    writer = WriterStub()
    asyncio.run(writer._revise_agent_analysis(data))
    assert data["agent_investigation"]["investigation_plan"][0]["status"] == "blocked"
    assert data["risk_assessment"] == before["risk_assessment"]
    assert data["critic_feedback"] == before["critic_feedback"]
    prompt = writer.call_llm.call_args.kwargs
    assert "investigation_plan" in prompt["user_prompt"] and "不得新增/改写问题" in prompt["user_prompt"]
    assert "冻结底稿" in prompt["system_prompt"]


def test_archived_ql_passing_source_quote_is_now_protocol_failure_without_rewriting_archive():
    directory = Path(__file__).resolve().parents[1] / "eval/agent_e2e_pack/runs/STAGE2-live-002/ql"
    checkpoint = json.loads((directory / "checkpoint-summary.json").read_text(encoding="utf-8"))
    receipt = checkpoint["state"]["quality_review"]
    report = (directory / "report.md").read_text(encoding="utf-8")
    check = next(row for row in receipt["analysis_checks"] if row["id"] == "receipt_reconciliation")
    assert check["status"] == "supported" and check["report_quote"] not in report
    raw = {"overall_assessment": {"quality_score": receipt["score"], "verdict": receipt["verdict"]},
           "issues": [], "analysis_checks": receipt["analysis_checks"]}
    checked = enforce_analysis_review(raw, report)
    assert checked["degraded"] and not checked["analysis_review_validated"]
    assert any(row["location"] == "receipt_reconciliation" and row["issue_type"] == "review_not_executed"
               for row in checked["issues"])
    assert not any(row["issue_type"] == "analysis_quality_error" for row in checked["issues"])


def test_archived_t01_exploratory_assertion_is_explicitly_in_review_not_exempted_by_labels():
    directory = Path(__file__).resolve().parents[1] / "eval/agent_e2e_pack/runs/STAGE2-live-002/t01"
    checkpoint = json.loads((directory / "checkpoint-summary.json").read_text(encoding="utf-8"))
    archived = checkpoint["state"]
    report = (directory / "report.md").read_text(encoding="utf-8")
    raw = wire_review(with_plan=False)
    for row in raw["checks"]:
        row["report_quote"] = "调查员概述"
    critic = CriticStub(raw)
    review_state = {**state(), "agent_investigation": archived["agent_investigation"], "final_report": report}
    asyncio.run(critic._review_agent_content(review_state, report))
    prompt = critic.call_llm.call_args.kwargs
    context = json.loads(prompt["user_prompt"])
    assert context["claims_to_audit"]["summary"] == archived["agent_investigation"]["summary"]
    assert "绝不豁免" in prompt["system_prompt"] and "否定主因" in prompt["system_prompt"]
    assert "来源写出的答案冒充报告" in COMPACT_REVIEW_PROMPT
