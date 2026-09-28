"""Question-driven investigation uses real tools/receipts, not model checkmarks."""
import asyncio
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.question_ledger import create_plan, coverage
from service.deep_research_v2.investigator import investigate, evidence_context, InvestigationBudget
from service.deep_research_v2.investigation_report import append_investigation_report
from service.deep_research_v2.investigation_tools import public_notebook
from service.deep_research_v2.research_outcome import build_research_outcome
from test_agent_workbench_tools import make_tools, execute, calculation_args
from test_research_outcome import clean_state


def plan(required=True):
    return {"questions": [{"question": "应收账款增长多少，回款还需什么验证？",
                           "done_when": "比较两期应收金额，计算增幅并区分内部数据与独立核验。",
                           "calculation_required": required}]}


def answer(sid, **overrides):
    return {"question_id": "p1", "status": "answered", "answer": "应收增加20万元，增幅20%；真实性需另行核验。",
            "citations": [{"source_id": sid, "quote_id": "q1"}, {"source_id": sid, "quote_id": "q2"}],
            "calculation_ids": ["c1"], "limitations": "内部材料尚未独立核验。", **overrides}


def prepared(required=True):
    tool, sid, _ = make_tools()
    execute(tool, "plan_investigation", plan(required))
    read = execute(tool, "read_source", {"source_id": sid})
    return tool, sid, read


def test_questions_have_server_ids_and_cannot_be_replaced_or_preanswered():
    notebook = {}
    args = plan()
    args["questions"][0].update(id="fake", status="answered", citations=[{"quote": "fake"}])
    result = create_plan(notebook, args)
    assert result["investigation_plan"][0]["id"] == "p1"
    assert result["investigation_plan"][0]["status"] == "open"
    assert not result["investigation_plan"][0]["citations"]
    assert not create_plan(notebook, args)["progress"]
    before = copy.deepcopy(notebook)
    for key, value in (("question", "换成更容易的问题"), ("done_when", "无需任何依据"), ("calculation_required", False)):
        changed = copy.deepcopy(args)
        changed["questions"][0][key] = value
        with pytest.raises(ValueError, match="不可删除"):
            create_plan(notebook, changed)
    assert notebook == before


@pytest.mark.parametrize("questions", [None, [], [{}], [plan()["questions"][0]] * 7,
    [plan()["questions"][0]] * 2, [{**plan()["questions"][0], "calculation_required": "true"}],
    [{**plan()["questions"][0], "question": "q" * 301}], [{**plan()["questions"][0], "done_when": ""}]])
def test_invalid_plan_never_partially_changes_state(questions):
    notebook = {}
    with pytest.raises(ValueError):
        create_plan(notebook, {"questions": questions})
    assert notebook == {}


def test_answer_requires_real_read_quotes_and_calculation_then_reaches_report_and_context():
    tool, sid, read = prepared()
    original_financial = copy.deepcopy((tool.state["field_checks"], tool.state["risk_assessment"]))
    with pytest.raises(ValueError, match="先调用calculate"):
        execute(tool, "address_question", answer(sid, calculation_ids=[]))
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    result = execute(tool, "address_question", answer(sid))
    assert result["progress"] and result["question"]["status"] == "answered"
    assert coverage(tool.notebook) == {"total": 1, "answered": 1, "blocked": 0, "open": 0}
    assert not execute(tool, "address_question", answer(sid))["progress"]
    context = evidence_context(tool.notebook)
    assert context["investigation_plan"][0]["answer"] == answer(sid)["answer"]
    assert len(context["cited_evidence"]) == 2
    assert public_notebook(tool.notebook)["investigation_plan"] == tool.notebook["investigation_plan"]
    report = append_investigation_report("规则评级不可改写", tool.notebook)
    assert "p1 [已回答，待复核]" in report and "计算底稿：c1" in report
    assert answer(sid)["answer"] in report and read["quote_options"]["q1"] in report
    assert original_financial == (tool.state["field_checks"], tool.state["risk_assessment"])


@pytest.mark.parametrize("override", [
    {"question_id": "evil"}, {"status": "approved"}, {"answer": ""},
    {"citations": []}, {"citations": [{"source_id": "other", "quote_id": "q1"}]},
    {"citations": [{"source_id": "s1", "quote_id": "invented"}]},
    {"calculation_ids": ["not-a-workpaper"]}, {"status": "blocked", "limitations": ""},
])
def test_bad_answer_is_atomic(override):
    tool, sid, read = prepared()
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    before = copy.deepcopy(tool.notebook)
    with pytest.raises(ValueError):
        execute(tool, "address_question", answer(sid, **override))
    assert tool.notebook == before


def test_cached_calculation_does_not_bypass_revoked_source_access():
    tool, sid, read = prepared()
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    tool.state["kb_scope"] = []
    before = copy.deepcopy(tool.notebook)
    with pytest.raises(ValueError, match="授权"):
        execute(tool, "address_question", answer(sid, status="blocked", citations=[]))
    assert tool.notebook == before


def test_blocked_without_quotes_is_a_material_gap_not_an_answer():
    tool, sid, _ = prepared()
    execute(tool, "address_question", {"question_id": "p1", "status": "blocked",
        "answer": "已读内部摘要；尚不能验证银行入账。", "limitations": "需银行回单核对付款人与入账日期。"})
    assert coverage(tool.notebook)["answered"] == 0
    assert coverage(tool.notebook)["blocked"] == 1
    report = append_investigation_report("报告", tool.notebook)
    assert "资料不足，暂不能完整回答" in report


def test_required_plan_and_question_answer_cannot_be_skipped_by_finish():
    tool, sid, _ = make_tools()
    tool.notebook["plan_required"] = True
    choices = iter([
        {"action": "finish", "arguments": {"summary": "已经全部完成"}},
        {"action": "plan_investigation", "arguments": plan()},
        {"action": "finish", "arguments": {"summary": "仍然没有真正回答"}},
        {"action": "finish", "arguments": {"summary": "本轮未完成p1调查，作为部分报告交付", "partial": True}},
    ])
    async def choose(_, context):
        if not context["investigation_plan"]:
            assert list(context["tools"]) == ["plan_investigation"]
        return next(choices)
    result = asyncio.run(investigate(brief={"query": "调查"}, tools=tool.definitions(), choose=choose,
        execute=tool.execute, notebook=tool.notebook, critique=False, budget=InvestigationBudget(max_steps=5)))
    assert result["status"] == "completed" and result["partial"]
    assert result["investigation_plan"][0]["status"] == "open"
    assert len([a for a in result["actions"] if a["action"] == "invalid_action"]) == 2


def test_real_loop_plans_reads_calculates_answers_and_review_can_reopen():
    tool, sid, _ = make_tools()
    tool.notebook["plan_required"] = True
    calls = 0
    async def choose(prompt, context):
        nonlocal calls
        if "最多提出两个" in prompt:
            assert context["investigation_plan"][0]["status"] == "answered"
            return {"questions": ["真实性仍需核验"], "reopen_question_ids": ["p1"]}
        sequence = [
            {"action": "plan_investigation", "arguments": plan()},
            {"action": "read_source", "arguments": {"source_id": sid}},
            {"action": "calculate", "arguments": calculation_args(sid, {"q1": "120万元", "q2": "100万元"})},
            {"action": "address_question", "arguments": answer(sid)},
            {"action": "finish", "arguments": {"summary": "按内部材料测算，待核验"}},
            {"action": "address_question", "arguments": answer(sid, status="blocked", limitations="缺银行回单，无法核实回款真实性。")},
            {"action": "finish", "arguments": {"summary": "已测算内部增幅，真实性暂不能回答"}},
        ]
        decision = sequence[calls]
        calls += 1
        return decision
    result = asyncio.run(investigate(brief={"query": "核对增长并核验真实性"}, tools=tool.definitions(),
        choose=choose, execute=tool.execute, notebook=tool.notebook, budget=InvestigationBudget(max_steps=9)))
    assert result["status"] == "completed" and result["partial"]
    assert result["finish_reviews"] == [{"questions": ["真实性仍需核验"], "reopened": ["p1"]}]
    assert result["investigation_plan"][0]["status"] == "blocked"
    assert result["calculations"][0]["result"] == "20"


@pytest.mark.parametrize("status,expected", [("open", "restricted"), ("blocked", "partial"), ("answered", "ready")])
def test_delivery_does_not_confuse_loop_completion_with_question_completion(status, expected):
    state = clean_state()
    create_plan(state["agent_investigation"], plan(False))
    state["agent_investigation"]["plan_required"] = True
    state["agent_investigation"]["investigation_plan"][0].update(status=status, limitations="待核验")
    # A complete independent review is not a replacement for unanswered work.
    state["quality_review"]["question_checks"] = [{"id": "p1", "status": "supported", "reason": "如实披露",
        "report_quote": "原文", "needs_more_evidence": False, "followup_question": ""}]
    assert build_research_outcome(state)["report_status"] == expected
    assert state["risk_assessment"]["credit_recommendation"]["recommendable"] is True


def test_missing_required_plan_and_invalid_answer_status_fail_closed_without_altering_risk():
    state = clean_state()
    state["agent_investigation"]["plan_required"] = True
    assert build_research_outcome(state)["report_status"] == "restricted"
    state["agent_investigation"]["investigation_plan"] = [{"id": "p1", "status": "invented"}]
    assert coverage(state["agent_investigation"])["open"] == 1
    assert build_research_outcome(state)["report_status"] == "restricted"
