import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.investigation_tools import InvestigationTools, public_notebook
from service.deep_research_v2.investigation_report import append_investigation_report
from service.deep_research_v2.web_reader import public_target, TextReader

DOC = "2c36860c-2e62-4a7e-b74c-ec33b5782c12"


def setup_tools():
    state = {"query": "调查回款", "search_local": True, "search_web": False,
             "field_checks": [{"field_id": "cash_flow", "status": "unverified"}],
             "kb_scope": [{"kb_id": "kb1", "collection": "authorized"}]}
    scout = SimpleNamespace(
        add_message=lambda *a: None, _retain_corpus_for_investigation=lambda *a: None,
        _annotate_statement_scope=lambda *a: None,
        _analyze_search_results=AsyncMock(return_value={"field_evidence": [], "extracted_facts": []}),
        milvus_service=SimpleNamespace(get_document_chunks=lambda *a: [
            {"chunk_index": 1, "content": "企业现金流下降，需要调查回款情况。"},
            {"chunk_index": 2, "content": "应收账款账龄超过一年增加，待核对期后回款。"}]),
    )
    tools = InvestigationTools(scout, state)
    sid, _ = tools.add_source({"is_local": True, "kb_id": "kb1", "doc_id": DOC,
                               "chunk_index": 1, "summary": "企业现金流下降，需要调查回款情况。",
                               "title": "年报", "url": f"local://kb/kb1/{DOC}"})
    return tools, sid


def test_read_and_record_does_not_change_rating_or_checks():
    tools, sid = setup_tools()
    original = copy.deepcopy(tools.state["field_checks"])
    read = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    assert read["evidence_feedback"]["status"] == "not_requested"
    tools.scout._analyze_search_results.assert_not_called()
    result = asyncio.run(tools.execute("record_finding", {
        "source_id": sid, "claim": "现金流下降可能与回款有关", "kind": "support",
        "quote": "企业现金流下降，需要调查回款情况。"}))
    assert result["progress"]
    assert tools.state["field_checks"] == original
    assert result["finding"]["verified"] is False
    assert result["finding"]["citation_status"] == "located"
    assert result["finding"]["inference_status"] == "not_reviewed"
    extracted = asyncio.run(tools.execute("extract_evidence", {"source_id": sid}))
    assert isinstance(extracted["evidence_feedback"]["allowed_field_ids"], list)
    assert "record_finding" in extracted["evidence_feedback"]["analysis_boundary"]
    assert tools.scout._analyze_search_results.call_args.kwargs["all_active_fields"] is True


def test_adjacent_chunk_is_read_only_from_authorized_document():
    tools, sid = setup_tools()
    asyncio.run(tools.execute("read_source", {"source_id": sid}))
    result = asyncio.run(tools.execute("read_source", {"source_id": sid, "chunk_index": 2}))
    assert "账龄" in result["text"]
    assert result["source_id"] != sid
    assert tools.sources[result["source_id"]]["doc_id"] == DOC
    invalid = asyncio.run(tools.execute("record_finding", {
            "source_id": result["source_id"], "claim": "不能将前一片段冒充当前来源",
            "quote": "企业现金流下降，需要调查回款情况。"}))
    assert invalid["ok"] is False and "引文" in invalid["error"]


def test_extraction_failure_preserves_readable_text_without_verification():
    tools, sid = setup_tools()
    original = copy.deepcopy(tools.state["field_checks"])
    tools.scout._analyze_search_results.side_effect = RuntimeError("provider unavailable")
    result = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    assert result["ok"] and "现金流" in result["text"]
    extracted = asyncio.run(tools.execute("extract_evidence", {"source_id": sid}))
    assert not extracted["ok"] and "RuntimeError" in extracted["evidence_feedback"]["error"]
    assert tools.sources[sid]["read"] is True
    assert tools.state["field_checks"] == original
    assert not tools.state.get("rag_evidence_candidates")


def test_extraction_cancellation_propagates():
    tools, sid = setup_tools()
    asyncio.run(tools.execute("read_source", {"source_id": sid}))
    tools.scout._analyze_search_results.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tools.execute("extract_evidence", {"source_id": sid}))


@pytest.mark.parametrize("invalid", ["unknown", None, []])
def test_cannot_read_unknown_source(invalid):
    tools, _ = setup_tools()
    result = asyncio.run(tools.execute("read_source", {"source_id": invalid}))
    assert not result["ok"] and result["sources"]


def test_revoked_scope_and_forged_quote_are_rejected():
    tools, sid = setup_tools()
    with pytest.raises(ValueError, match="先 read_source"):
        asyncio.run(tools.execute("record_finding", {"source_id": sid, "claim": "需要调查原文事实"}))
    asyncio.run(tools.execute("read_source", {"source_id": sid}))
    result = asyncio.run(tools.execute("record_finding", {"source_id": sid, "claim": "没有任何经营风险存在", "quote": "不存在任何经营风险"}))
    assert not result["ok"] and "引文" in result["error"]
    tools.state["kb_scope"] = []
    with pytest.raises(ValueError, match="授权"):
        asyncio.run(tools.execute("read_source", {"source_id": sid, "chunk_index": 2}))


def test_disabled_web_tool_never_runs():
    tools, _ = setup_tools()
    with pytest.raises(ValueError, match="未授权"):
        asyncio.run(tools.execute("search_web", {"query": "企业"}))


def test_quote_id_uses_exact_read_text_and_rejects_unknown_id():
    tools, sid = setup_tools()
    read = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    qid, quote = next(iter(read["quote_options"].items()))
    result = asyncio.run(tools.execute("record_finding", {
        "source_id": sid, "claim": "现金流变化需要进一步调查", "quote_id": qid}))
    assert result["finding"]["quote"] == quote and quote in read["text"]
    assert result["finding"]["verified"] is False
    bad = asyncio.run(tools.execute("record_finding", {
        "source_id": sid, "claim": "现金流变化需要进一步调查", "quote_id": "forged"}))
    assert not bad["ok"]
    tools.scout._analyze_search_results.assert_not_called()


def test_repeat_read_is_cached_and_rechecks_scope():
    tools, sid = setup_tools()
    first = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    second = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    assert second["cached"] and not second["progress"]
    assert first["text"] == second["text"]
    assert tools.scout._analyze_search_results.call_count == 0
    tools.state["kb_scope"] = []
    with pytest.raises(ValueError, match="授权"):
        asyncio.run(tools.execute("read_source", {"source_id": sid}))


def test_failed_extraction_is_not_silently_retried_on_reread():
    tools, sid = setup_tools()
    tools.scout._analyze_search_results.side_effect = RuntimeError("offline")
    asyncio.run(tools.execute("read_source", {"source_id": sid}))
    asyncio.run(tools.execute("extract_evidence", {"source_id": sid}))
    result = asyncio.run(tools.execute("read_source", {"source_id": sid}))
    extracted = asyncio.run(tools.execute("extract_evidence", {"source_id": sid}))
    assert result["cached"] and extracted["cached"]
    assert "RuntimeError" in extracted["evidence_feedback"]["error"]
    assert tools.scout._analyze_search_results.call_count == 1


def test_public_projection_omits_raw_sources_and_report_is_idempotent():
    notebook = {"status": "stalled", "sources": {"secret": "raw"}, "actions": [{"secret": "raw"}],
                "findings": [{"claim": "<script>alert(1)</script>", "quote": "原文", "source_id": "s1"}],
                "questions": ["需要期后回款明细"]}
    assert "sources" not in public_notebook(notebook)
    assert "actions" not in public_notebook(notebook)
    report = append_investigation_report("原评级", notebook)
    assert append_investigation_report(report, notebook) == report
    assert "<script>" not in report
    assert "原评级" in report and "期后回款" in report


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "198.18.0.20", "::1"])
def test_web_reader_rejects_private_and_fake_ip(monkeypatch, ip):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 443))])
    with pytest.raises(ValueError, match="公网"):
        public_target("https://example.com/report")


def test_html_reader_excludes_script_and_style():
    parser = TextReader()
    parser.feed("<html><head><style>hidden</style></head><body><p>营业收入</p><script>bad()</script></body></html>")
    assert "营业收入" in "".join(parser.parts)
    assert "hidden" not in "".join(parser.parts) and "bad()" not in "".join(parser.parts)
