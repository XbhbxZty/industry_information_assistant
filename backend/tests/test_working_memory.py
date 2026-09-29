"""Memory projection preserves meaning; no LLM or external service required."""
import asyncio
from copy import deepcopy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.investigator import evidence_context, investigate, InvestigationBudget
from service.deep_research_v2.working_memory import working_memory
from service.deep_research_v2.investigation_tools import public_notebook
from test_agent_workbench_tools import make_tools, execute


def fixture():
    quote = "本期应收账款余额为120万元，分类一致性待核对。"
    citation = {"source_id": "s1", "quote_id": "q1", "quote": quote, "title": "台账"}
    question = {"id": "p1", "question": "是否增长？", "done_when": "比较且披露限制",
                "status": "answered", "calculation_required": True,
                "answer": "增长20%，但不能据此归因。", "limitations": "未核实，期间需核对。",
                "citations": [citation], "calculation_ids": ["c1"]}
    calculation = {"id": "c1", "expression": "current / 100", "result": "1.2", "result_unit": "倍",
                   "variables": {"current": {**citation, "value": "120", "unit": "万元", "period": "本期"}},
                   "limitations": "仅演示除法，不验证因果。", "verified": False}
    notebook = {"investigation_plan": [question], "calculations": [calculation],
                "findings": [], "questions": ["核对期间"], "sources": {
                    "s1": {"read": True, "read_texts": [quote], "quote_options": {"q1": quote}}}}
    actions = [
        {"action": "calculate", "arguments": {"expression": calculation["expression"],
                                               "variables": calculation["variables"]},
         "result": {"ok": True, "progress": True, "calculation": calculation}},
        {"action": "address_question", "arguments": {"answer": question["answer"]},
         "result": {"ok": True, "progress": True, "question": question}},
    ]
    notebook["actions"] = deepcopy(actions)
    context = {"brief": {"query": "解释变化，不预设风险", "known_facts": [], "followup_questions": []},
               "tools": {"recall_evidence": "回取"}, "questions": notebook["questions"],
               "findings": notebook["findings"], "recent_actions": deepcopy(actions),
               "observation": deepcopy(actions[-1]["result"]), **evidence_context(notebook)}
    return context, notebook


def test_projection_is_pure_and_shared_exports_unchanged():
    context, notebook = fixture()
    before = deepcopy((context, notebook, evidence_context(notebook), public_notebook(notebook)))
    projected = working_memory(context, notebook)
    assert (context, notebook, evidence_context(notebook), public_notebook(notebook)) == before
    assert projected["brief"] == context["brief"]
    assert "用户任务" in projected["memory_contract"]["brief.query"]
    assert "模型" in projected["memory_contract"]["investigation_plan/questions"]
    assert "不自动" in projected["memory_contract"]["brief.known_facts/followup_questions"]
    projected["investigation_plan"][0]["answer"] = "modified by caller"
    assert context["investigation_plan"][0]["answer"] != "modified by caller"


def test_exact_state_deduplicated_without_rewriting_answers_or_workpapers():
    context, notebook = fixture()
    memory = working_memory(context, notebook)
    assert memory["observation"] == {"ok": True, "progress": True, "question_ref": "p1"}
    assert memory["recent_actions"][-1]["result"] == {"observation_ref": "observation"}
    assert memory["recent_actions"][0]["arguments"] == {"calculation_ref": "c1"}
    assert memory["recent_actions"][0]["result"]["calculation_ref"] == "c1"
    for key in ("question", "done_when", "status", "answer", "limitations", "calculation_ids", "calculation_required"):
        assert memory["investigation_plan"][0][key] == context["investigation_plan"][0][key]
    for key in ("expression", "result", "result_unit", "limitations", "verified"):
        assert memory["calculations"][0][key] == context["calculations"][0][key]
    assert json.dumps(memory, ensure_ascii=False).count("增长20%，但不能据此归因。") == 1
    variable = memory["calculations"][0]["variables"]["current"]
    assert (variable["value"], variable["unit"], variable["period"]) == ("120", "万元", "本期")
    assert variable["quote_ref"] == "cited_evidence/read_evidence"
    assert "quote" not in variable
    assert memory["cited_evidence"][0]["quote"] == context["calculations"][0]["variables"]["current"]["quote"]


def test_old_answer_is_not_referenced_as_new_answer():
    context, notebook = fixture()
    context["investigation_plan"][0]["answer"] = "已修改的最新答复"
    memory = working_memory(context, notebook)
    assert memory["observation"]["question"]["answer"] == "增长20%，但不能据此归因。"
    assert "question_ref" not in memory["observation"]
    assert memory["recent_actions"][-1]["arguments"] == context["recent_actions"][-1]["arguments"]


def test_latest_failed_arguments_and_structured_repair_remain_complete():
    context, notebook = fixture()
    failed_args = {"expression": "abs(raw)", "variables": {"raw": {"value": "-100", "quote_id": "q1"}}}
    error = {"ok": False, "error": "invalid", "error_code": "calculation_expression_invalid",
             "field_path": "expression", "repair": {"instruction": "保留符号", "actions": []},
             "retry_suppressed": True, "executed": False}
    context["recent_actions"].append({"action": "retry_suppressed", "attempted_action": "calculate",
                                      "arguments": failed_args, "result": error})
    context["observation"] = error
    memory = working_memory(context, notebook)
    assert memory["recent_actions"][-1]["arguments"] == failed_args
    assert memory["recent_actions"][-1]["attempted_action"] == "calculate"
    assert memory["observation"] == error


@pytest.mark.parametrize("citation", [
    {"source_id": "s1", "quote": "旧引文没有ID"},
    {"source_id": "s1", "quote_id": "q1", "quote": "同ID但不匹配的旧文"},
    {"source_id": "another", "quote_id": "q1", "quote": "本期应收账款余额为120万元，分类一致性待核对。"},
])
def test_legacy_or_mismatched_citations_not_shortened(citation):
    context, notebook = fixture()
    context["investigation_plan"][0]["citations"] = [citation]
    memory = working_memory(context, notebook)
    assert memory["investigation_plan"][0]["citations"] == [citation]


def test_read_observation_and_unindexed_short_text_never_disappear():
    context, notebook = fixture()
    text = "有效长引文。" * 400 + "\n非本案"
    receipt = {"ok": True, "progress": True, "source_id": "s1", "text": text,
               "quote_options": {"q2": "有效长引文。"}}
    context["recent_actions"] = [{"action": "read_source", "arguments": {"source_id": "s1"}, "result": receipt}]
    context["observation"] = deepcopy(receipt)
    memory = working_memory(context, notebook)
    assert memory["observation"] == receipt
    assert memory["recent_actions"][0]["result"] == {"observation_ref": "observation"}
    context["observation"] = {"review_questions": ["核对否定信息"]}
    memory = working_memory(context, notebook)
    assert memory["recent_actions"][0]["result"] == receipt


def test_evicted_source_has_explicit_recall_entry_and_no_fake_read():
    context, notebook = fixture()
    for i in range(2, 18):
        notebook["sources"][f"s{i}"] = {"read": True, "read_texts": [f"来源{i}已读原文"],
                                          "quote_options": {"q1": f"来源{i}已读原文"}}
    context.update(evidence_context(notebook))
    memory = working_memory(context, notebook)
    assert "s1" not in {item["source_id"] for item in memory["read_evidence"]}
    index = memory["source_inventory"][0]["memory"]
    assert index == {"quote_count": 1, "visible_quote_count": 0, "in_read_evidence": False,
                     "text_truncated": False,
                     "recall": {"action": "recall_evidence", "arguments": {"source_id": "s1", "start": 0}}}
    context["tools"] = {}
    assert "recall" not in working_memory(context, notebook)["source_inventory"][0]["memory"]


def test_plan_receipt_ref_only_when_entire_plan_matches():
    context, notebook = fixture()
    context["observation"] = {"ok": True, "investigation_plan": deepcopy(context["investigation_plan"])}
    assert working_memory(context, notebook)["observation"] == {
        "ok": True, "investigation_plan_ref": "investigation_plan"}
    context["observation"]["investigation_plan"][0]["status"] = "open"
    assert "investigation_plan" in working_memory(context, notebook)["observation"]


def test_unknown_tool_result_preserved():
    context, notebook = fixture()
    context["observation"] = {"progress": True, "finding": "账期延长"}
    assert working_memory(context, notebook)["observation"] == context["observation"]


@pytest.mark.parametrize("finish", [True, False])
def test_projection_reaches_normal_review_and_closeout(finish):
    calls = []
    notebook = {"sources": {"s1": {"read": True, "read_texts": ["已读原文尚未核实"]}}}
    async def choose(prompt, context):
        assert context["memory_contract"]
        assert context["read_evidence"][0]["text"] == "已读原文尚未核实"
        calls.append(prompt)
        if "最多提出两个" in prompt:
            assert context["proposed_finish"]["summary"] == "仅有条件判断"
            return {"questions": []}
        if "调查工具已停止" in prompt:
            assert "recall" not in context["source_inventory"][0]["memory"]
            return {"summary": "尚未完成"}
        return {"action": "finish", "arguments": {"summary": "仅有条件判断"}} if finish else {"action": "bad"}
    async def never_execute(*_):
        pytest.fail("not a tool action")
    result = asyncio.run(investigate(brief={"query": "调查"}, tools={}, choose=choose,
                                    execute=never_execute, notebook=notebook,
                                    budget=InvestigationBudget(max_steps=2 if finish else 1)))
    assert len(calls) == 2
    assert result["status"] == ("completed" if finish else "step_limit")


def test_actual_recall_reaches_next_choice_but_repeats_still_stall():
    tool, sid, _ = make_tools()
    original = execute(tool, "read_source", {"source_id": sid})
    count = 0
    async def choose(prompt, context):
        nonlocal count
        if "调查工具已停止" in prompt:
            return {"summary": "没有新证据，仍待核查"}
        if count:
            assert context["observation"]["quote_options"] == {"q1": original["quote_options"]["q1"]}
            assert context["observation"]["progress"] is False
            assert context["observation"]["memory_only"] is True
            assert context["recovery"]["consecutive_no_progress"] == count
        count += 1
        return {"action": "recall_evidence", "arguments": {"source_id": sid, "quote_ids": ["q1"]}}
    before = deepcopy(tool.sources)
    result = asyncio.run(investigate(brief={"query": "调查"}, tools=tool.definitions(), choose=choose,
                                    execute=tool.execute, notebook=tool.notebook, critique=False))
    assert count == 3
    assert result["status"] == "stalled"
    assert all(action["action"] == "recall_evidence" for action in result["actions"])
    assert tool.sources == before
