"""Actionable citation failures never read, replace evidence or cross scope."""
import copy
import json
from unittest.mock import Mock

import pytest

from test_agent_workbench_tools import make_tools, execute, calculation_args
from test_calculation_tool import request
from service.deep_research_v2.action_errors import ActionError, resolve_at
from service.deep_research_v2.calculation_tool import calculate_workpaper


def failure(call):
    with pytest.raises(ActionError) as caught:
        call()
    result = caught.value.as_result()
    assert result["ok"] is False and result["progress"] is False
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    return result


def plan(tool):
    execute(tool, "plan_investigation", {"questions": [{
        "question": "两期应收账款如何变化？", "done_when": "引用两期数字并解释变化。",
        "calculation_required": False,
    }]})


def answer(sid, **overrides):
    return {"question_id": "p1", "status": "answered", "answer": "应收账款增加，尚需核验。",
            "citations": [{"source_id": sid, "quote_id": "q1"}], **overrides}


def test_unread_recovery_gives_exact_read_action_without_reading_or_guessing_quote():
    tool, sid, _ = make_tools()
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: tool.resolve_citation(sid, "q88"))
    assert result["error_code"] == "citation_source_unread"
    assert result["repair"]["source_id"] == sid and result["repair"]["quote_id"] == "q88"
    assert result["repair"]["actions"] == [{"action": "read_source", "arguments": {"source_id": sid}}]
    assert "quote_options" not in result["repair"]
    assert tool.notebook == before
    tool.scout.milvus_service.get_document_chunks.assert_not_called()


@pytest.mark.parametrize("source_id", ["s999", None, {}, "secret<instruction>" * 100])
def test_unknown_source_never_echoes_arguments_or_notebook_metadata(source_id):
    tool, sid, _ = make_tools()
    tool.sources[sid]["title"] = "PRIVATE TITLE"
    result = failure(lambda: tool.resolve_citation(source_id, "RAW PRIVATE QUOTE"))
    assert result["error_code"] == "citation_source_unavailable"
    assert set(result["repair"]) == {"instruction", "actions"}
    assert result["repair"]["actions"] == [{"action": "list_materials", "arguments": {}}]
    assert "PRIVATE" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("revoke", ["scope", "local", "web", "invalid_document"])
def test_scope_rechecked_before_read_or_quote_details_and_matches_unknown(revoke):
    tool, sid, _ = make_tools()
    execute(tool, "read_source", {"source_id": sid})
    if revoke == "scope":
        tool.state["kb_scope"] = []
    elif revoke == "local":
        tool.state["search_local"] = False
    elif revoke == "web":
        tool.sources[sid]["is_local"] = False
    else:
        tool.sources[sid]["doc_id"] = "PRIVATE BAD DOC"
    missing = failure(lambda: tool.resolve_citation("s999", "q1"))
    revoked = failure(lambda: tool.resolve_citation(sid, "q1"))
    assert revoked == missing
    assert "quote_options" not in revoked["repair"]
    assert "available_quote_ids" not in revoked["repair"]
    assert "PRIVATE" not in json.dumps(revoked)
    assert all(a["action"] in tool.definitions() for a in revoked["repair"]["actions"])


def test_invalid_quote_only_returns_bounded_exact_actually_read_options():
    tool, sid, _ = make_tools()
    execute(tool, "read_source", {"source_id": sid})
    source = tool.sources[sid]
    quotes = {f"q{i}": f"第{i}条" + "已读内容" * 247 for i in range(1, 131)}
    source["read_texts"] = list(quotes.values())
    source["quote_options"] = {"unread": "PRIVATE UNREAD ORIGINAL", "empty": "", **quotes}
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: tool.resolve_citation(sid, "q999"))
    repair = result["repair"]
    assert result["error_code"] == "citation_quote_not_found"
    assert repair["source_id"] == sid and repair["quote_id"] == "q999"
    assert len(repair["available_quote_ids"]) == 120
    assert len(repair["quote_options"]) == 4
    assert sum(map(len, repair["quote_options"].values())) <= 4000
    assert all(quotes[qid] == text for qid, text in repair["quote_options"].items())
    assert "PRIVATE" not in json.dumps(result)
    assert "unread" not in repair["available_quote_ids"] and "empty" not in repair["available_quote_ids"]
    assert tool.notebook == before


def test_quote_error_with_no_valid_options_suggests_read_without_reading():
    tool, sid, _ = make_tools()
    tool.sources[sid].update(read=True, read_texts=["已经读过的内容。"], quote_options={"q1": "PRIVATE UNREAD"})
    result = failure(lambda: tool.resolve_citation(sid, "q1"))
    assert result["repair"]["quote_options"] == {}
    assert result["repair"]["available_quote_ids"] == []
    assert result["repair"]["actions"] == [{"action": "read_source", "arguments": {"source_id": sid}}]
    tool.scout.milvus_service.get_document_chunks.assert_not_called()


def test_calculate_preserves_repair_and_variable_path_without_committing():
    tool, sid, _ = make_tools()
    args = request()
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: execute(tool, "calculate", args))
    assert result["error_code"] == "citation_source_unread"
    assert result["field_path"] == "variables.current"
    assert result["repair"]["source_id"] == sid
    assert result["repair"]["actions"][0]["action"] == "read_source"
    assert tool.notebook == before


def test_calculate_missing_second_quote_retains_actual_options_atomically():
    tool, sid, _ = make_tools()
    read = execute(tool, "read_source", {"source_id": sid})
    args = calculation_args(sid, read["quote_options"])
    args["variables"]["previous"]["quote_id"] = "q99"
    before = copy.deepcopy((tool.notebook, args, tool.state["field_checks"], tool.state["risk_assessment"]))
    result = failure(lambda: execute(tool, "calculate", args))
    assert result["error_code"] == "citation_quote_not_found"
    assert result["field_path"] == "variables.previous"
    assert result["repair"]["quote_options"] == read["quote_options"]
    assert before == (tool.notebook, args, tool.state["field_checks"], tool.state["risk_assessment"])


@pytest.mark.parametrize("exception", [RuntimeError("private token=SECRET"), PermissionError("private path SECRET")])
def test_arbitrary_resolver_exceptions_are_sanitized_in_calculation_and_ledger(exception):
    resolver = Mock(side_effect=exception)
    for call in (lambda: calculate_workpaper(request(), resolver),
                 lambda: resolve_at(resolver, "s1", "q1", "citations[0]")):
        result = failure(call)
        assert result["error_code"] == "citation_resolution_failed"
        assert "SECRET" not in json.dumps(result)
        assert not result["repair"]["actions"]


def test_question_reference_error_names_failed_index_and_preserves_original_question():
    tool, sid, _ = make_tools()
    plan(tool)
    execute(tool, "read_source", {"source_id": sid})
    args = answer(sid, citations=[{"source_id": sid, "quote_id": "q1"}, {"source_id": sid, "quote_id": "q99"}])
    before = copy.deepcopy((tool.notebook, args))
    result = failure(lambda: execute(tool, "address_question", args))
    assert result["field_path"] == "citations[1]"
    assert result["repair"]["quote_id"] == "q99"
    assert (tool.notebook, args) == before


def test_question_stale_calculation_citation_names_workpaper_variable_path():
    tool, sid, _ = make_tools()
    plan(tool)
    read = execute(tool, "read_source", {"source_id": sid})
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    del tool.sources[sid]["quote_options"]["q2"]
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: execute(tool, "address_question", answer(sid, calculation_ids=["c1"])))
    assert result["error_code"] == "citation_quote_not_found"
    assert result["field_path"] == "calculation_ids[0].variables.previous"
    assert tool.notebook == before


def test_question_changed_calculation_quote_does_not_silently_repair_old_workpaper():
    tool, sid, _ = make_tools()
    plan(tool)
    read = execute(tool, "read_source", {"source_id": sid})
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    tool.notebook["calculations"][0]["variables"]["previous"]["quote"] = "旧底稿里的不同原文。"
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: execute(tool, "address_question", answer(sid, calculation_ids=["c1"])))
    assert result["error_code"] == "citation_changed"
    assert result["field_path"] == "calculation_ids[0].variables.previous"
    assert "calculate" in result["repair"]["instruction"]
    assert tool.notebook == before


@pytest.mark.parametrize("legacy", [False, True])
def test_record_finding_preserves_repair_in_returned_receipt_for_ids_and_legacy_quote(legacy):
    tool, sid, _ = make_tools()
    execute(tool, "read_source", {"source_id": sid})
    args = {"claim": "应收变化仍须核对具体证据", "source_id": sid}
    args.update({"quote": "不在已读材料里的任意断言"} if legacy else {"quote_id": "q99"})
    before = copy.deepcopy(tool.notebook)
    result = execute(tool, "record_finding", args)
    assert result["error_code"] == "citation_quote_not_found"
    assert result["field_path"] == ("quote" if legacy else "citations[0]")
    assert result["repair"]["available_quote_ids"] == ["q1", "q2"]
    assert tool.notebook == before


def test_legacy_quote_still_requires_current_scope_and_exact_read_substring():
    tool, sid, _ = make_tools()
    read = execute(tool, "read_source", {"source_id": sid})
    args = {"claim": "应收变化仍须独立核对主体与期间", "source_id": sid, "quote": read["quote_options"]["q1"]}
    assert execute(tool, "record_finding", args)["ok"]
    tool.state["kb_scope"] = []
    before = copy.deepcopy(tool.notebook)
    result = failure(lambda: execute(tool, "record_finding", args))
    assert result["error_code"] == "citation_source_unavailable"
    assert "quote_options" not in result["repair"]
    assert tool.notebook == before


def test_unknown_read_action_does_not_enumerate_revoked_notebook_sources():
    tool, sid, _ = make_tools()
    tool.sources[sid]["title"] = "PRIVATE REVOKED TITLE"
    tool.state["kb_scope"] = []
    result = execute(tool, "read_source", {"source_id": "s999"})
    assert result["error_code"] == "citation_source_unavailable"
    assert "sources" not in result and "PRIVATE" not in json.dumps(result)


def test_error_receipts_and_field_path_wrapping_do_not_share_mutable_repair_data():
    original = ActionError("citation_source_unread", "请先阅读", repair={"actions": [{"action": "read_source", "arguments": {"source_id": "s1"}}]})
    wrapped = original.at("citations[0]")
    result = wrapped.as_result()
    result["repair"]["actions"][0]["arguments"]["source_id"] = "s999"
    assert original.repair == wrapped.repair
    assert wrapped.as_result()["repair"]["actions"][0]["arguments"]["source_id"] == "s1"


@pytest.mark.parametrize("action", ["address_question", "record_finding"])
@pytest.mark.parametrize("ids,path", [
    (["c1", "c999"], "calculation_ids[1]"),
    (["c1", {"private": "DO NOT ECHO"}], "calculation_ids[1]"),
    ("c1", "calculation_ids"), (None, "calculation_ids"),
    (["c1"] * 7, "calculation_ids"),
])
def test_invalid_calculation_ids_give_safe_targeted_recovery_without_committing(action, ids, path):
    tool, sid, _ = make_tools()
    plan(tool)
    read = execute(tool, "read_source", {"source_id": sid})
    execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    args = (answer(sid, calculation_ids=ids) if action == "address_question" else {
        "claim": "应收变化有待结合回款继续核查", "source_id": sid, "quote_id": "q1", "calculation_ids": ids,
    })
    before = copy.deepcopy((tool.notebook, args, tool.state["field_checks"], tool.state["risk_assessment"]))
    result = failure(lambda: execute(tool, action, args))
    assert result["error_code"] == "calculation_reference_invalid"
    assert result["field_path"] == path
    assert result["repair"]["available_calculation_ids"] == ["c1"]
    assert "calculate" in result["repair"]["instruction"]
    assert result["repair"]["actions"] == []
    assert "DO NOT ECHO" not in json.dumps(result)
    assert before == (tool.notebook, args, tool.state["field_checks"], tool.state["risk_assessment"])


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("omit", [False, True])
def test_required_calculation_missing_ids_never_implicitly_selects_existing_or_answers(existing, omit):
    tool, sid, _ = make_tools()
    plan(tool)
    tool.notebook["investigation_plan"][0]["calculation_required"] = True
    read = execute(tool, "read_source", {"source_id": sid})
    if existing:
        execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    args = answer(sid)
    if not omit:
        args["calculation_ids"] = []
    before = copy.deepcopy((tool.notebook, args))
    result = failure(lambda: execute(tool, "address_question", args))
    assert result["error_code"] == "calculation_required"
    assert result["field_path"] == "calculation_ids"
    assert "先调用calculate" in result["error"]
    assert result["repair"]["available_calculation_ids"] == (["c1"] if existing else [])
    assert result["repair"]["actions"] == []
    assert before == (tool.notebook, args)
    assert tool.notebook["investigation_plan"][0]["status"] == "open"


def test_available_calculation_ids_are_bounded_actual_ids_not_workpaper_content():
    tool, sid, _ = make_tools()
    plan(tool)
    execute(tool, "read_source", {"source_id": sid})
    tool.notebook["calculations"] = [
        {"id": f"c{i}", "label": "PRIVATE WORKPAPER", "variables": {}} for i in range(1, 15)
    ]
    result = failure(lambda: execute(tool, "address_question", answer(sid, calculation_ids=["c999"])))
    assert result["repair"]["available_calculation_ids"] == [f"c{i}" for i in range(1, 13)]
    assert "PRIVATE" not in json.dumps(result)
