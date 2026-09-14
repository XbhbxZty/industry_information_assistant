"""Behavioral tests: evidence changes actions, failures don't become conclusions."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.investigator import investigate, InvestigationBudget


def test_observation_changes_next_query_and_critique_triggers_followup():
    calls = []
    notebook = {}

    async def choose(prompt, context):
        if "最多提出两个" in prompt:
            return {"questions": ["是否存在短期备货的替代解释？"]}
        if context["observation"].get("review_questions"):
            return {"action": "search", "arguments": {"query": "库存与预付款"}}
        if not calls:
            return {"action": "search", "arguments": {"query": "现金流"}}
        if calls[-1] == "现金流":
            assert context["observation"]["finding"] == "账期延长"
            return {"action": "search", "arguments": {"query": "应收账款账龄"}}
        return {"action": "finish", "arguments": {"summary": "尚需回款资料", "missing_materials": ["期后回款明细"]}}

    async def execute(action, args):
        calls.append(args["query"])
        return {"progress": True, "finding": "账期延长"}

    asyncio.run(investigate(brief={}, tools={"search": "搜索"}, choose=choose,
                            execute=execute, notebook=notebook))
    assert calls == ["现金流", "应收账款账龄", "库存与预付款"]
    assert notebook["status"] == "completed"
    assert notebook["missing_materials"] == ["期后回款明细"]


def test_repeated_action_stops_without_repeating_io_or_claiming_completion():
    calls = []
    async def choose(*args):
        return {"action": "search", "arguments": {"query": "same"}}
    async def execute(*args):
        calls.append(1)
        return {"ok": False, "progress": False, "error": "索引不可用"}
    notebook = asyncio.run(investigate(brief={}, tools={"search": ""}, choose=choose,
                                      execute=execute, notebook={}))
    assert len(calls) == 1
    assert notebook["status"] == "stalled"
    assert "summary" not in notebook


def test_disabled_tool_is_not_executed():
    async def choose(*args):
        return {"action": "web", "arguments": {}}
    async def execute(*args):
        pytest.fail("disabled tool executed")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook={}, budget=InvestigationBudget(max_steps=2)))
    assert result["status"] == "step_limit"


def test_cancellation_propagates():
    async def choose(*args):
        raise asyncio.CancelledError()
    async def execute(*args):
        pytest.fail("cancelled run executed a tool")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute, notebook={}))


def test_timeout_is_a_failure_not_an_empty_search():
    async def choose(*args):
        await asyncio.sleep(1)
    async def execute(*args):
        pytest.fail("timed out run executed a tool")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook={}, budget=InvestigationBudget(call_timeout=.001, max_steps=1)))
    assert result["status"] == "step_limit"
    assert result["actions"][0]["result"]["ok"] is False


def test_followup_pass_cannot_reset_step_budget():
    async def choose(*args):
        pytest.fail("exhausted budget called model")
    async def execute(*args):
        pytest.fail("exhausted budget called tool")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook={"steps_used": 12}))
    assert result["status"] == "step_limit"
    assert result["steps_used"] == 12


def test_exhausted_time_budget_does_not_call_model():
    async def choose(*args):
        pytest.fail("exhausted time called model")
    async def execute(*args):
        pytest.fail("exhausted time called tool")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook={}, budget=InvestigationBudget(max_seconds=0)))
    assert result["status"] == "time_limit"
