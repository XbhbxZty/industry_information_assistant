"""Read-only evidence recall preserves provenance, scope and progress accounting."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from test_agent_workbench_tools import execute, make_tools
from service.deep_research_v2.action_errors import ActionError


def prepared(count=56):
    text = "\n".join(f"第 {index:03} 项已读材料原文，仍需独立核验。" for index in range(1, count + 1))
    tool, sid, messages = make_tools([{"chunk_index": 0, "content": text}])
    read = execute(tool, "read_source", {"source_id": sid})
    assert len(read["quote_options"]) == count
    return tool, sid, messages


def assert_read_only(tool, before, chunks_before, messages, messages_before):
    assert tool.state == before
    assert tool._chunks == chunks_before
    assert messages == messages_before


def error_result(tool, args):
    with pytest.raises(ActionError) as caught:
        execute(tool, "recall_evidence", args)
    return caught.value.as_result()


def test_recall_is_available_and_pages_to_middle_quotes_omitted_by_old_projection():
    tool, sid, messages = prepared()
    assert "recall_evidence" in tool.definitions()
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    seen, start = {}, 0
    while start is not None:
        result = execute(tool, "recall_evidence", {"source_id": sid, "start": start})
        assert result["ok"] is True and result["memory_only"] is True
        assert result["progress"] is False and result["source_id"] == sid
        assert result["quote_count"] == 56
        assert len(result["quote_options"]) <= 8
        assert sum(map(len, result["quote_options"].values())) <= 6000
        assert not set(result["quote_options"]) & set(seen)
        seen.update(result["quote_options"])
        start = result["next_start"]
    assert seen == tool.sources[sid]["quote_options"]
    assert seen["q25"] == "第 025 项已读材料原文，仍需独立核验。"
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_exact_quote_ids_preserve_requested_order_and_do_not_read_or_publish():
    tool, sid, messages = prepared()
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    tool.read_source = AsyncMock(side_effect=AssertionError("must not re-read"))
    tool.document_chunks = AsyncMock(side_effect=AssertionError("must not load chunks"))
    tool.extract_evidence = AsyncMock(side_effect=AssertionError("must not extract"))
    tool.scout.milvus_service.get_document_chunks.reset_mock()
    tool.scout._retain_corpus_for_investigation.reset_mock()
    tool.scout._annotate_statement_scope.reset_mock()
    expected = {qid: tool.sources[sid]["quote_options"][qid] for qid in ["q31", "q25"]}
    first = execute(tool, "recall_evidence", {"source_id": sid, "quote_ids": ["q31", "q25"]})
    second = execute(tool, "recall_evidence", {"source_id": sid, "quote_ids": ["q31", "q25"]})
    assert first == second
    assert first["quote_options"] == expected
    assert list(first["quote_options"]) == ["q31", "q25"]
    assert first["next_start"] is None and first["progress"] is False
    assert first["memory_only"] is True
    tool.read_source.assert_not_called()
    tool.document_chunks.assert_not_called()
    tool.extract_evidence.assert_not_called()
    tool.scout.milvus_service.get_document_chunks.assert_not_called()
    tool.scout._retain_corpus_for_investigation.assert_not_called()
    tool.scout._annotate_statement_scope.assert_not_called()
    tool.scout._analyze_search_results.assert_not_called()
    assert_read_only(tool, before, chunks_before, messages, messages_before)


@pytest.mark.parametrize("change", ["scope", "local_disabled", "web_disabled", "invalid_doc"])
@pytest.mark.parametrize("selection", [{}, {"quote_ids": ["q25"]}])
def test_revoked_source_reuses_unknown_source_boundary_without_metadata(change, selection):
    tool, sid, messages = prepared()
    tool.sources[sid]["title"] = "PRIVATE REVOKED TITLE"
    if change == "scope":
        tool.state["kb_scope"] = []
    elif change == "local_disabled":
        tool.state["search_local"] = False
    elif change == "web_disabled":
        tool.sources[sid]["is_local"] = False
    else:
        tool.sources[sid]["doc_id"] = "PRIVATE INVALID DOCUMENT"
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    revoked = error_result(tool, {"source_id": sid, **selection})
    unknown = error_result(tool, {"source_id": "s999", **selection})
    assert revoked == unknown
    assert revoked["error_code"] == "citation_source_unavailable"
    assert "PRIVATE" not in json.dumps(revoked)
    assert "quote_options" not in revoked["repair"]
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_web_recall_never_fetches_url_and_remains_read_only():
    tool, sid, messages = prepared(2)
    tool.sources[sid].update(is_local=False, url="https://example.invalid/not-to-fetch")
    tool.state["search_web"] = True
    tool.read_source = AsyncMock(side_effect=AssertionError("must not fetch webpage"))
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    result = execute(tool, "recall_evidence", {"source_id": sid})
    assert result["quote_options"] == tool.sources[sid]["quote_options"]
    tool.read_source.assert_not_called()
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_unread_source_does_not_promote_retrieval_snippet_or_read_automatically():
    tool, sid, messages = make_tools()
    tool.sources[sid]["quote_options"] = {"q1": "PRIVATE RETRIEVAL SNIPPET"}
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    result = error_result(tool, {"source_id": sid, "quote_ids": ["q1"]})
    assert result["error_code"] == "citation_source_unread"
    assert result["repair"]["actions"] == [{"action": "read_source", "arguments": {"source_id": sid}}]
    assert "PRIVATE RETRIEVAL" not in json.dumps(result, ensure_ascii=False)
    tool.scout.milvus_service.get_document_chunks.assert_not_called()
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_unknown_quote_rejects_whole_request_without_replacement_or_mutation():
    tool, sid, messages = prepared()
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    result = error_result(tool, {"source_id": sid, "quote_ids": ["q25", "q999"]})
    assert result["error_code"] == "citation_quote_not_found"
    assert result["repair"]["quote_id"] == "q999"
    assert result["ok"] is False and result["progress"] is False
    assert "quote_options" not in result  # Repair examples are not a recalled result.
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_unlocated_or_empty_stored_quotes_are_never_recalled():
    tool, sid, messages = prepared(2)
    tool.sources[sid]["quote_options"].update(q3="PRIVATE UNREAD QUOTE", q4="", q5=None)
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    result = execute(tool, "recall_evidence", {"source_id": sid})
    assert result["quote_count"] == 2 and set(result["quote_options"]) == {"q1", "q2"}
    missing = error_result(tool, {"source_id": sid, "quote_ids": ["q3"]})
    assert "PRIVATE UNREAD" not in json.dumps(missing)
    assert_read_only(tool, before, chunks_before, messages, messages_before)


@pytest.mark.parametrize("options", [None, {}, {"q1": "unread legacy quote"}])
def test_legacy_read_source_without_valid_quotes_returns_explicit_empty_result(options):
    tool, sid, messages = prepared(1)
    if options is None:
        del tool.sources[sid]["quote_options"]
    else:
        tool.sources[sid]["quote_options"] = options
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    result = execute(tool, "recall_evidence", {"source_id": sid})
    assert result["ok"] is True and result["progress"] is False
    assert result["quote_count"] == 0 and result["quote_options"] == {}
    assert result["next_start"] is None and "read_source" in result["note"]
    with pytest.raises(ValueError, match="start"):
        execute(tool, "recall_evidence", {"source_id": sid, "start": 1})
    assert_read_only(tool, before, chunks_before, messages, messages_before)


@pytest.mark.parametrize("extra", [
    {"quote_ids": []}, {"quote_ids": "q1"}, {"quote_ids": None},
    {"quote_ids": ["q1"] * 7}, {"quote_ids": ["q1", "q1"]},
    {"quote_ids": [True]}, {"quote_ids": [{}]}, {"quote_ids": [""]},
    {"quote_ids": ["q1"], "start": 0}, {"quote_ids": ["q1"], "start": None},
    {"start": -1}, {"start": True}, {"start": 1.0}, {"start": "0"}, {"start": None},
    {"start": 56}, {"start": 1000}, {"offset": 0},
])
def test_invalid_selection_parameters_are_atomic(extra):
    tool, sid, messages = prepared()
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    with pytest.raises(ValueError):
        execute(tool, "recall_evidence", {"source_id": sid, **extra})
    assert_read_only(tool, before, chunks_before, messages, messages_before)


@pytest.mark.parametrize("args", [None, [], "s1", 0])
def test_nonobject_arguments_are_rejected(args):
    tool, _, _ = prepared(1)
    with pytest.raises(ValueError, match="recall_evidence"):
        asyncio.run(tool.execute("recall_evidence", args))


def test_pagination_total_character_cap_advances_by_actual_returned_count():
    tool, sid, messages = prepared(1)
    quotes = {f"q{i}": f"{i:03}" + "已" * 997 for i in range(1, 10)}
    tool.sources[sid].update(quote_options=quotes, read_texts=list(quotes.values()))
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    first = execute(tool, "recall_evidence", {"source_id": sid})
    assert list(first["quote_options"]) == [f"q{i}" for i in range(1, 7)]
    assert sum(map(len, first["quote_options"].values())) == 6000
    assert first["next_start"] == 6 and first["quote_count"] == 9
    last = execute(tool, "recall_evidence", {"source_id": sid, "start": first["next_start"]})
    assert list(last["quote_options"]) == ["q7", "q8", "q9"]
    assert last["next_start"] is None
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_exact_request_over_character_cap_is_rejected_without_truncation():
    tool, sid, messages = prepared(1)
    quotes = {f"q{i}": f"{i:03}" + "已" * 1197 for i in range(1, 7)}
    tool.sources[sid].update(quote_options=quotes, read_texts=list(quotes.values()))
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    with pytest.raises(ValueError, match="6000"):
        execute(tool, "recall_evidence", {"source_id": sid, "quote_ids": list(quotes)})
    assert_read_only(tool, before, chunks_before, messages, messages_before)


def test_oversized_legacy_quote_does_not_bypass_response_cap():
    tool, sid, messages = prepared(1)
    tool.sources[sid].update(quote_options={"q1": "已" * 6001}, read_texts=["已" * 6001])
    before, chunks_before, messages_before = copy.deepcopy((tool.state, tool._chunks, messages))
    with pytest.raises(ValueError, match="上限"):
        execute(tool, "recall_evidence", {"source_id": sid})
    assert_read_only(tool, before, chunks_before, messages, messages_before)
