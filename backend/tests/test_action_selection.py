"""Recommended actions stay distinct from permissions and substantive judgment."""
import asyncio
from copy import deepcopy

import pytest

from test_agent_workbench_tools import make_tools, execute, calculation_args
from test_question_ledger import plan, answer
from service.deep_research_v2.action_errors import ActionError
from service.deep_research_v2.action_selection import action_options, selectable_tools
from service.deep_research_v2.investigator import investigate, evidence_context, InvestigationBudget


def options(tool):
    return action_options(tool.notebook, selectable_tools(tool.notebook, tool.definitions()),
                          evidence_context(tool.notebook), {})


def run_loop(tool, choose, *, max_steps=12, execute_action=None):
    return asyncio.run(investigate(brief={}, tools=tool.definitions(), choose=choose,
                                  execute=execute_action or tool.execute, notebook=tool.notebook,
                                  critique=False, budget=InvestigationBudget(max_steps=max_steps)))


def test_plan_visibility_preserves_required_first_step_and_legacy_mode():
    tools = {"plan_investigation": "", "read_source": ""}
    assert selectable_tools({"plan_required": True}, tools) == {"plan_investigation": ""}
    assert selectable_tools({}, tools) == tools
    assert selectable_tools({"investigation_plan": [{"id": "p1"}]}, tools) == {"read_source": ""}
    assert len(tools) == 2
    notebook = {"plan_required": True}
    assert action_options(notebook, selectable_tools(notebook, tools), {}, {})["next_options"] == ["plan_investigation"]


def test_replanning_is_rejected_before_execution_with_original_plan_intact():
    tool, _, _ = make_tools()
    execute(tool, "plan_investigation", plan())
    original = deepcopy(tool.notebook["investigation_plan"])
    async def choose(_, context):
        assert "plan_investigation" not in context["tools"]
        if context["observation"]:
            assert context["observation"]["error_code"] == "plan_already_created"
            return {"action": "finish", "arguments": {"summary": "调查尚未完成", "partial": True}}
        return {"action": "plan_investigation", "arguments": plan(False)}
    async def never(*args):
        pytest.fail("plan recreation reached executor")
    result = run_loop(tool, choose, execute_action=never)
    assert result["investigation_plan"] == original
    assert result["partial"] is True


def test_page_candidates_follow_offsets_then_new_source_id_without_reset():
    # Identical text at different offsets still counts as new page coverage.
    text = "本期收入为120万元。" * 1600
    tool, sid, _ = make_tools([{ "chunk_index": 0, "content": text},
                              {"chunk_index": 7, "content": text}])
    first = options(tool)["read_candidates"][0]
    assert first["arguments"] == {"source_id": sid}
    execute(tool, first["action"], first["arguments"])
    pages = []
    while tool.sources[sid]["read_receipt"]["next_offset"] is not None:
        candidate = options(tool)["read_candidates"][0]
        assert candidate["action"] == "read_source"
        assert candidate["arguments"]["source_id"] == sid
        pages.append(candidate["arguments"]["offset"])
        assert execute(tool, candidate["action"], candidate["arguments"])["progress"]
    assert pages == [6000, 12000]
    candidate = options(tool)["read_candidates"][0]
    assert candidate == {"action": "read_next", "arguments": {"source_id": sid}, "purpose": "unread_chunk"}
    receipt = execute(tool, candidate["action"], candidate["arguments"])
    next_sid = receipt["source_id"]
    assert next_sid != sid and tool.sources[next_sid]["chunk_index"] == 7
    candidates = options(tool)["read_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["arguments"] == {"source_id": next_sid, "offset": 6000}
    assert execute(tool, candidates[0]["action"], candidates[0]["arguments"])["progress"]


def test_tail_first_read_recommends_zero_offset_not_next_chunk():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "材料数据为120万元。" * 700},
                              {"chunk_index": 4, "content": "其他数据100万元。"}])
    execute(tool, "read_source", {"source_id": sid, "offset": 6000})
    candidates = options(tool)["read_candidates"]
    assert candidates == [{"action": "read_source", "arguments": {"source_id": sid, "offset": 0},
                           "purpose": "continue_page"}]


def test_cached_revisit_remains_available_even_during_recovery_but_not_recommended():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "收入为120万元，尚待独立核验。"}])
    execute(tool, "read_source", {"source_id": sid})
    observed = []
    async def choose(prompt, context):
        if "recovery" not in context:
            return {"summary": "未完成", "missing_materials": []}
        observed.append(context["recovery"]["active"])
        assert "read_source" in context["tools"]
        assert not context["action_options"]["read_candidates"]
        assert "read_source" not in context["action_options"]["next_options"]
        return {"action": "read_source", "arguments": {"source_id": sid}}
    result = run_loop(tool, choose)
    assert observed == [False, False, True]
    assert result["status"] == "stalled" and result["steps_used"] == 3
    assert all(a["result"]["cached"] and not a["result"]["progress"] for a in result["actions"])


def test_new_search_remains_available_after_two_failures_without_enabling_web():
    tool, _, _ = make_tools()
    calls = []
    async def choose(_, context):
        assert "search_web" not in context["tools"]
        assert "search_web" not in context["action_options"]["next_options"]
        if len(calls) == 3:
            return {"action": "finish", "arguments": {"summary": "新检索已返回资料", "partial": True}}
        if len(calls) == 2:
            assert context["recovery"]["active"]
            assert "search_local" in context["tools"]
        return {"action": "search_local", "arguments": {"query": "新线索" if len(calls) == 2 else "原线索"}}
    async def search(action, args):
        calls.append(args["query"])
        return {"ok": len(calls) == 3, "progress": len(calls) == 3}
    result = run_loop(tool, choose, execute_action=search)
    assert calls == ["原线索", "原线索", "新线索"]
    assert result["status"] == "completed"


@pytest.mark.parametrize("returned", [False, True])
def test_identical_validation_failure_is_suppressed_without_automatic_repair(returned):
    tool, sid, _ = make_tools()
    calls = []
    error = ActionError("citation_source_unread", "先阅读", field_path="citations[1]",
                        repair={"actions": [{"action": "read_source", "arguments": {"source_id": sid}}]})
    async def choose(_, context):
        if context["observation"]:
            assert context["observation"]["field_path"] == "citations[1]"
            assert context["action_options"]["repair_actions"] == error.repair["actions"]
        return {"action": "record_finding", "arguments": {"claim": "同一个无效引用"}}
    async def fail(*args):
        calls.append(args)
        if returned:
            return error.as_result()
        raise error
    result = run_loop(tool, choose, execute_action=fail)
    assert len(calls) == 1
    assert result["status"] == "stalled" and result["steps_used"] == 3
    assert [a["action"] for a in result["actions"]][1:] == ["retry_suppressed", "retry_suppressed"]
    assert result["actions"][-1]["result"]["executed"] is False
    assert not tool.sources[sid]["read"]


def test_unread_citation_can_be_repaired_then_identical_answer_retried_atomically():
    tool, sid, _ = make_tools()
    execute(tool, "plan_investigation", plan(False))
    tool.notebook["plan_required"] = True
    original = deepcopy(tool.notebook["investigation_plan"])
    risk = deepcopy(tool.state["risk_assessment"])
    arguments = answer(sid, calculation_ids=[])
    step = 0
    async def choose(_, context):
        nonlocal step
        step += 1
        if step in (1, 2):
            assert tool.notebook["investigation_plan"] == original
            return {"action": "address_question", "arguments": arguments}
        if step == 3:
            assert tool.notebook["investigation_plan"] == original
            assert context["observation"]["retry_suppressed"]
            assert context["observation"]["field_path"] == "citations[0]"
            return context["action_options"]["repair_actions"][0]
        if step == 4:
            assert context["observation"]["progress"]
            return {"action": "address_question", "arguments": arguments}
        return {"action": "finish", "arguments": {"summary": "分析已记录，尚待核验"}}
    result = run_loop(tool, choose)
    assert result["status"] == "completed" and result["steps_used"] == 5
    assert result["investigation_plan"][0]["status"] == "answered"
    assert len(result["question_updates"]) == 1
    assert tool.state["risk_assessment"] == risk
    assert tool.state["field_checks"][0]["status"] == "unverified"


def test_missing_calculation_repair_creates_real_workpaper_before_answer_retry():
    tool, sid, _ = make_tools()
    execute(tool, "plan_investigation", plan())
    read = execute(tool, "read_source", {"source_id": sid})
    step = 0
    async def choose(_, context):
        nonlocal step
        step += 1
        if step in (1, 3):
            return {"action": "address_question", "arguments": answer(sid)}
        if step == 2:
            assert context["observation"]["error_code"] == "calculation_reference_invalid"
            assert context["observation"]["repair"]["available_calculation_ids"] == []
            assert tool.notebook["investigation_plan"][0]["status"] == "open"
            return {"action": "calculate", "arguments": calculation_args(sid, read["quote_options"])}
        return {"action": "finish", "arguments": {"summary": "已完成内部数据的增幅计算"}}
    result = run_loop(tool, choose)
    assert result["status"] == "completed"
    assert result["investigation_plan"][0]["calculation_ids"] == ["c1"]
    assert len(result["calculations"]) == 1


@pytest.mark.parametrize("returned", [False, True])
def test_unknown_resolver_failure_retains_one_retry_but_not_unbounded_retries(returned):
    tool, _, _ = make_tools()
    calls = []
    async def choose(_, context):
        return {"action": "calculate", "arguments": {"expression": "current-prior"}}
    async def transient(*args):
        calls.append(args)
        error = ActionError("citation_resolution_failed", "引文校验未完成")
        if returned:
            return error.as_result()
        raise error
    result = run_loop(tool, choose, execute_action=transient)
    assert len(calls) == 2
    assert result["status"] == "stalled" and result["steps_used"] == 3
    assert result["actions"][-1]["action"] == "retry_suppressed"


def test_transient_resolver_failure_can_succeed_on_unchanged_retry():
    tool, _, _ = make_tools()
    calls = []
    async def choose(_, context):
        if len(calls) == 2:
            return {"action": "finish", "arguments": {"summary": "瞬时错误已恢复", "partial": True}}
        return {"action": "calculate", "arguments": {"expression": "current-prior"}}
    async def transient(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ActionError("citation_resolution_failed", "引文校验未完成")
        return {"ok": True, "progress": True}
    result = run_loop(tool, choose, execute_action=transient)
    assert result["status"] == "completed" and len(calls) == 2
    assert all(a["action"] != "retry_suppressed" for a in result["actions"])
