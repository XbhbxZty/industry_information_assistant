"""Behavioral tests: evidence changes actions, failures don't become conclusions."""
import asyncio
import sys
from pathlib import Path

import pytest


def test_cached_reads_lead_to_bounded_closeout_options():
    notebook = {"sources": {"s1": {"read": True, "title": "资料", "read_texts": ["已读原文"]}}}
    calls = []
    async def choose(prompt, context):
        if context["recovery"]["active"]:
            assert set(context["tools"]) == {"record_finding"}
            assert context["recovery"]["next_options"] == ["record_finding", "finish"]
            return {"action": "finish", "arguments": {"summary": "已有原文，仍需核验"}}
        return {"action": "read_source", "arguments": {"source_id": "s1"}}
    async def execute(action, args):
        calls.append(action)
        return {"ok": True, "cached": True, "progress": False, "text": "已读原文"}
    result = asyncio.run(investigate(brief={}, tools={"read_source": "", "record_finding": ""},
                                    choose=choose, execute=execute, notebook=notebook, critique=False))
    assert calls == ["read_source", "read_source"]
    assert result["status"] == "completed"


def test_invalid_action_keeps_brief_action_reason():
    async def choose(prompt, context):
        return {"action": "unknown", "reason": "核对分类口径"}
    async def execute(*args):
        pytest.fail("invalid tool ran")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook={}, budget=InvestigationBudget(max_steps=1)))
    assert result["actions"][0]["reason"] == "核对分类口径"


def test_source_memory_survives_action_window_and_reaches_review():
    notebook = {"sources": {
        "read1": {"title": "对比表", "read": True, "read_texts": ["上年收入为30，本年为40"], "extraction_error": "timeout"},
        "unread1": {"title": "到账明细", "read": False, "summary": "分类到账记录"},
    }}
    async def choose(prompt, context):
        assert len(context["source_inventory"]) == 2
        assert context["read_evidence"][0]["text"] == "上年收入为30，本年为40"
        assert context["source_inventory"][1]["status"] == "retrieved_unread"
        if "最多提出两个" in prompt:
            assert context["proposed_finish"]["summary"] == "需阅读到账明细"
            return {"questions": []}
        return {"action": "finish", "arguments": {"summary": "需阅读到账明细"}}
    async def execute(*args):
        pytest.fail("no tool needed")
    asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute, notebook=notebook))


def test_recovery_sees_unread_materials_and_original_text():
    notebook = {"findings": [{"claim": "有资料"}], "sources": {
        "a": {"title": "已有台账", "read": False, "summary": "到账数据"}}}
    async def choose(prompt, context):
        if "调查工具已停止" in prompt:
            assert context["source_inventory"][0]["status"] == "retrieved_unread"
            return {"summary": "台账已提供但未读", "missing_materials": []}
        return {"action": "bad"}
    async def execute(*args):
        pytest.fail("invalid tool")
    result = asyncio.run(investigate(brief={}, tools={}, choose=choose, execute=execute,
                                    notebook=notebook, budget=InvestigationBudget(max_steps=1)))
    assert result["summary"] == "台账已提供但未读"
    assert result["status"] == "step_limit"

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
    assert len(calls) == 2  # Recovery suppresses a third unproductive retry, even with no directory.
    assert notebook["status"] == "stalled"
    assert "调查未完成" in notebook["summary"]


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


def test_stalled_run_closes_out_existing_findings_without_claiming_completion():
    choices = 0
    async def choose(prompt, context):
        nonlocal choices
        choices += 1
        if "调查工具已停止" in prompt:
            return {"summary": "已有材料只支持识别疑点", "missing_materials": ["银行流水，用于核对回款"]}
        return {"action": "search", "arguments": {"query": str(choices)}}
    async def execute(action, args):
        return {"ok": False, "progress": False, "error": "没有新来源"}
    result = asyncio.run(investigate(brief={}, tools={"search": ""}, choose=choose,
                                    execute=execute,
                                    notebook={"findings": [{"claim": "已有发现"}]}))
    assert result["status"] == "stalled"
    assert result["summary"] == "已有材料只支持识别疑点"
    assert "银行流水" in result["missing_materials"][0]
