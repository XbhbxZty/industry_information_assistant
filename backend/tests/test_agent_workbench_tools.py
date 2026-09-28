"""Actual investigation tools: material navigation, cited synthesis and workpapers."""
import asyncio
import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2 import material_catalog
from service.deep_research_v2.investigation_tools import InvestigationTools, public_notebook
from service.deep_research_v2.investigation_report import append_investigation_report
from service.deep_research_v2.investigator import InvestigationBudget, evidence_context, investigate


KB = "f2c69e49-a811-4968-9c43-5d8e68d4a611"
DOC = "2c36860c-2e62-4a7e-b74c-ec33b5782c12"
OTHER_DOC = "9cc1978a-ef0a-43ae-a2b4-9833f14c0d11"
OWNER = "f0da2e98-caf2-4734-afb4-ac08d990b193"
FIRST_TEXT = "本期应收账款为120万元。上期应收账款为100万元。"
SECOND_TEXT = "一年以上应收账款余额为30万元，相关回款凭证尚待核对。"
THIRD_TEXT = "截至次年三月收回原账面应收款20万元，回款主体尚待独立核实。"


def make_tools(rows=None):
    messages = []
    state = {
        "query": "解释应收账款和回款变化", "_user_id": OWNER,
        "search_local": True, "search_web": False,
        "kb_scope": [{"kb_id": KB, "collection": "owned_collection", "kb_name": "样例材料"}],
        "field_checks": [{"field_id": "cash_flow", "status": "unverified"}],
        "risk_assessment": {"level": "资料不足", "requires_human_review": True},
    }
    if rows is None:
        rows = [{"chunk_index": 0, "content": FIRST_TEXT},
                {"chunk_index": 1, "content": SECOND_TEXT},
                {"chunk_index": 2, "content": THIRD_TEXT}]
    scout = SimpleNamespace(
        add_message=lambda _, event, payload: messages.append((event, copy.deepcopy(payload))),
        _retain_corpus_for_investigation=Mock(), _annotate_statement_scope=Mock(),
        _analyze_search_results=AsyncMock(return_value={"field_evidence": [], "extracted_facts": []}),
        milvus_service=SimpleNamespace(get_document_chunks=Mock(return_value=copy.deepcopy(rows))),
    )
    tool = InvestigationTools(scout, state)
    sid, _ = tool.add_source({
        "is_local": True, "kb_id": KB, "doc_id": DOC, "chunk_index": 0,
        "summary": "检索摘录可能被截断，原文必须重新读取。", "title": "年度财务材料",
        "url": f"local://kb/{KB}/{DOC}",
    })
    return tool, sid, messages


def execute(tool, action, args=None):
    return asyncio.run(tool.execute(action, args or {}))


def catalog_fixture(monkeypatch, *, status="completed", chunk_count=3):
    catalog = {"documents": [{"doc_id": DOC, "kb_id": KB, "title": "年度财务材料",
                              "index_status": status, "chunk_count": chunk_count}], "truncated": False}
    loader = Mock(return_value=catalog)
    monkeypatch.setattr(material_catalog, "load_material_catalog", loader)
    return loader


def read_pair(tool, sid):
    first = execute(tool, "read_source", {"source_id": sid})
    second = execute(tool, "read_next", {"source_id": sid})
    return first, second


def calculation_args(sid, quotes):
    current = next(key for key, text in quotes.items() if "120万元" in text)
    previous = next(key for key, text in quotes.items() if "100万元" in text)
    return {"label": "应收账款同比变化", "expression": "(current - previous) / previous * 100",
            "variables": {
                "current": {"value": "120", "source_id": sid, "quote_id": current,
                            "period": "本期", "unit": "万元"},
                "previous": {"value": "100", "source_id": sid, "quote_id": previous,
                             "period": "上期", "unit": "万元"}},
            "result_unit": "%", "limitations": "尚未审计；分类一致性待核对。"}


def test_catalog_exposes_processing_status_but_is_not_read_evidence(monkeypatch):
    loader = catalog_fixture(monkeypatch, status="pending")
    tool, sid, messages = make_tools()
    result = execute(tool, "list_materials")
    loader.assert_called_once_with(tool.state["kb_scope"], OWNER)
    assert result["ok"] and result["documents"][0]["source_id"] == sid
    assert result["documents"][0]["index_status"] == "pending"
    assert not tool.sources[sid]["read"]
    assert not evidence_context(tool.notebook)["read_evidence"]
    public = messages[-1][1]["material_coverage"]
    assert public["documents_total"] == 1 and public["read_chunks"] == 0
    assert "不是材料不存在" in result["note"]


@pytest.mark.parametrize("error", [PermissionError("not authorized"), RuntimeError("database offline")])
def test_catalog_failure_does_not_claim_no_materials(monkeypatch, error):
    monkeypatch.setattr(material_catalog, "load_material_catalog", Mock(side_effect=error))
    tool, _, _ = make_tools()
    result = execute(tool, "list_materials")
    assert not result["ok"] and not result["progress"]
    assert tool.notebook["catalog_status"] == "unavailable"
    assert "不能据此认定没有材料" in result["note"]
    assert "documents" not in result


def test_catalog_outside_authorized_scope_is_rejected_without_source_addition(monkeypatch):
    monkeypatch.setattr(material_catalog, "load_material_catalog", Mock(return_value={
        "documents": [{"kb_id": "foreign", "doc_id": OTHER_DOC, "title": "不可读资料"}],
        "truncated": False,
    }))
    tool, _, _ = make_tools()
    before = copy.deepcopy(tool.sources)
    with pytest.raises(ValueError, match="范围外"):
        execute(tool, "list_materials")
    assert tool.sources == before


def test_catalog_placeholder_reads_actual_index_not_search_preview(monkeypatch):
    catalog_fixture(monkeypatch)
    tool, sid, _ = make_tools()
    # Start with only the catalog-created placeholder, not a search hit.
    tool.sources.clear()
    catalog = execute(tool, "list_materials")
    sid = catalog["documents"][0]["source_id"]
    assert tool.sources[sid]["summary"] == ""
    result = execute(tool, "read_source", {"source_id": sid})
    assert result["source_id"] == sid and result["text"] == FIRST_TEXT
    assert len(tool.sources) == 1
    tool.scout.milvus_service.get_document_chunks.assert_called_once_with("owned_collection", DOC)
    tool.scout._analyze_search_results.assert_not_called()
    assert result["evidence_feedback"]["status"] == "not_requested"


def test_read_next_navigates_real_chunks_and_counts_only_observed_coverage(monkeypatch):
    catalog_fixture(monkeypatch)
    tool, sid, _ = make_tools()
    execute(tool, "list_materials")
    first = execute(tool, "read_source", {"source_id": sid})
    assert first["navigation"]["unread_chunk_indices"] == [1, 2]
    assert public_notebook(tool.notebook)["material_coverage"]["read_chunks"] == 1
    second = execute(tool, "read_next", {"source_id": sid})
    assert second["source_id"] != sid and second["text"] == SECOND_TEXT
    assert second["navigation"]["next_chunk_index"] == 2
    third = execute(tool, "read_next", {"source_id": second["source_id"]})
    assert third["text"] == THIRD_TEXT
    coverage = public_notebook(tool.notebook)["material_coverage"]
    assert coverage["documents_read"] == 1 and coverage["known_chunks"] == coverage["read_chunks"] == 3
    completed = execute(tool, "read_next", {"source_id": third["source_id"]})
    assert completed["ok"] and not completed["progress"]
    assert "独立核实" in completed["note"]
    assert tool.scout.milvus_service.get_document_chunks.call_count == 1


def test_long_chunk_reads_remaining_window_before_advancing_and_keeps_early_quotes(monkeypatch):
    catalog_fixture(monkeypatch, chunk_count=2)
    prefix = "第一窗口中的企业背景需要完整阅读。"
    long_text = prefix + "甲" * (6000 - len(prefix)) + "第二窗口显示回款30万元，仍须核实付款主体。"
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": long_text},
                              {"chunk_index": 1, "content": SECOND_TEXT}])
    execute(tool, "list_materials")
    first = execute(tool, "read_source", {"source_id": sid})
    early_quote = next(k for k, text in first["quote_options"].items() if text == prefix)
    assert first["next_offset"] == 6000 and first["total_chars"] == len(long_text)
    assert not tool.sources[sid]["read_complete"]
    assert public_notebook(tool.notebook)["material_coverage"]["read_chunks"] == 0
    second = execute(tool, "read_next", {"source_id": sid})
    assert second["source_id"] == sid and second["chunk_index"] == 0
    assert second["offset"] == 6000 and second["next_offset"] is None
    assert "回款30万元" in second["text"] and tool.sources[sid]["read_complete"]
    assert public_notebook(tool.notebook)["material_coverage"]["read_chunks"] == 1
    assert tool.resolve_citation(sid, early_quote)["quote"] == prefix
    third = execute(tool, "read_next", {"source_id": sid})
    assert third["chunk_index"] == 1 and third["source_id"] != sid
    assert public_notebook(tool.notebook)["material_coverage"]["read_chunks"] == 2


def test_identical_text_at_distinct_offsets_is_not_the_same_read_receipt():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "同" * 12000}])
    first = execute(tool, "read_source", {"source_id": sid})
    second = execute(tool, "read_next", {"source_id": sid})
    assert second["text"] == first["text"]
    assert second["offset"] == 6000 and second["next_offset"] is None
    assert second["progress"] and tool.sources[sid]["read_complete"]
    repeat = execute(tool, "read_source", {"source_id": sid, "offset": 6000})
    assert repeat["cached"] and not repeat["progress"]


def test_next_chunk_does_not_inherit_previous_chunks_coverage_ranges():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "甲" * 6000 + "乙" * 4000},
                              {"chunk_index": 1, "content": "丙" * 6000 + "丁" * 4000}])
    execute(tool, "read_source", {"source_id": sid})
    execute(tool, "read_next", {"source_id": sid})
    second_chunk = execute(tool, "read_next", {"source_id": sid})
    assert second_chunk["chunk_index"] == 1 and second_chunk["next_offset"] == 6000
    assert not tool.sources[second_chunk["source_id"]]["read_complete"]
    assert tool.sources[second_chunk["source_id"]]["read_ranges"] == [[0, 6000]]


def test_out_of_order_page_does_not_claim_unread_prefix_complete():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "甲" * 6000 + "乙" * 1000}])
    tail = execute(tool, "read_source", {"source_id": sid, "offset": 6000})
    assert tail["next_offset"] == 0 and not tool.sources[sid]["read_complete"]
    prefix = execute(tool, "read_next", {"source_id": sid})
    assert prefix["offset"] == 0 and prefix["next_offset"] is None
    assert tool.sources[sid]["read_complete"]


def test_read_window_limit_preserves_old_quotes_and_does_not_claim_remaining_text_read():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": "".join(letter * 6000 for letter in "甲乙丙丁戊己庚")}])
    first = execute(tool, "read_source", {"source_id": sid})
    qid, quote = next(iter(first["quote_options"].items()))
    for _ in range(5):
        assert execute(tool, "read_next", {"source_id": sid})["progress"]
    seventh = execute(tool, "read_next", {"source_id": sid})
    assert not seventh["ok"] and not seventh["progress"]
    assert "尚未读完" in seventh["error"]
    assert not tool.sources[sid]["read_complete"]
    assert tool.resolve_citation(sid, qid)["quote"] == quote


def test_explicit_extraction_uses_newly_read_page_and_only_reuses_same_page_receipt():
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": FIRST_TEXT + "甲" * (6000 - len(FIRST_TEXT)) + THIRD_TEXT}])
    execute(tool, "read_source", {"source_id": sid})
    tool.scout._analyze_search_results.assert_not_called()
    first = execute(tool, "extract_evidence", {"source_id": sid})
    assert first["ok"] and tool.scout._analyze_search_results.call_count == 1
    second_page = execute(tool, "read_next", {"source_id": sid})
    second = execute(tool, "extract_evidence", {"source_id": sid})
    assert second["ok"] and not second.get("cached", False)
    assert tool.scout._analyze_search_results.call_count == 2
    assert tool.scout._analyze_search_results.call_args.args[2][0]["summary"] == second_page["text"]
    cached = execute(tool, "extract_evidence", {"source_id": sid})
    assert cached["cached"] and not cached["progress"]
    assert tool.scout._analyze_search_results.call_count == 2


@pytest.mark.parametrize("rows", [[], [{"chunk_index": True, "content": FIRST_TEXT}],
    [{"chunk_index": -1, "content": FIRST_TEXT}], [{"chunk_index": 0, "content": None}],
    [{"chunk_index": 0, "content": FIRST_TEXT}, {"chunk_index": 0, "content": SECOND_TEXT}],
])
def test_missing_or_invalid_index_cannot_be_claimed_empty_or_read(rows):
    tool, sid, _ = make_tools(rows)
    with pytest.raises(ValueError):
        execute(tool, "read_source", {"source_id": sid})
    assert not tool.sources[sid]["read"]
    assert not tool.sources[sid]["read_texts"]
    assert not evidence_context(tool.notebook)["read_evidence"]
    tool.scout._analyze_search_results.assert_not_called()


def test_unknown_chunk_is_recoverable_and_does_not_create_fabricated_source():
    tool, sid, _ = make_tools()
    result = execute(tool, "read_source", {"source_id": sid, "chunk_index": 999})
    assert not result["ok"] and result["available_chunks"] == [0, 1, 2]
    assert len(tool.sources) == 1 and not tool.sources[sid]["read"]


def test_finding_combines_multiple_sources_and_bad_last_reference_fails_atomically():
    tool, sid, _ = make_tools()
    first, second = read_pair(tool, sid)
    refs = [{"source_id": result["source_id"], "quote_id": next(iter(result["quote_options"]))}
            for result in (first, second)]
    before = copy.deepcopy(tool.notebook.get("findings", []))
    invalid = execute(tool, "record_finding", {"claim": "应收增长伴随长账龄余额，需核实回款归属", "citations": [
        *refs, {"source_id": second["source_id"], "quote_id": "invented"}]})
    assert not invalid["ok"] and not invalid["progress"]
    assert tool.notebook.get("findings", []) == before
    valid = execute(tool, "record_finding", {"claim": "应收增长伴随长账龄余额，需核实回款归属", "citations": refs})
    assert valid["progress"] and len(valid["finding"]["citations"]) == 2
    assert valid["finding"]["verified"] is False
    assert valid["finding"]["source_id"] == sid  # Legacy single-citation consumers.


@pytest.mark.parametrize("key, ids", [("quote_id", "q1, q2"), ("quote_ids", ["q1", "q2"])])
def test_csv_and_array_quote_id_shorthand_preserve_exact_citations(key, ids):
    tool, sid, _ = make_tools()
    execute(tool, "read_source", {"source_id": sid})
    result = execute(tool, "record_finding", {"claim": "两期账面金额可用于计算差值", "source_id": sid, key: ids})
    assert result["progress"]
    assert [c["quote_id"] for c in result["finding"]["citations"]] == ["q1", "q2"]


def test_near_duplicate_wording_is_not_progress_but_new_calculation_basis_is():
    tool, sid, _ = make_tools()
    read = execute(tool, "read_source", {"source_id": sid})
    args = {"claim": "应收账款同比增长，应进一步核对期后回款情况。", "source_id": sid, "quote_ids": ["q1", "q2"]}
    assert execute(tool, "record_finding", args)["progress"]
    variant = {**args, "claim": "应收账款同比增长，仍应进一步核对期后回款情况。"}
    duplicate = execute(tool, "record_finding", variant)
    assert duplicate["ok"] and not duplicate["progress"]
    assert len(tool.notebook["findings"]) == 1
    assert variant["claim"] in tool.notebook["findings"][0]["wording_variants"]
    computed = execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    fresh = execute(tool, "record_finding", {**variant, "calculation_ids": [computed["calculation"]["id"]]})
    assert fresh["progress"] and len(tool.notebook["findings"]) == 2


def test_calculation_integrates_citations_context_report_without_changing_risk_state():
    tool, sid, _ = make_tools()
    before = copy.deepcopy((tool.state["field_checks"], tool.state["risk_assessment"]))
    read = execute(tool, "read_source", {"source_id": sid})
    args = calculation_args(sid, read["quote_options"])
    result = execute(tool, "calculate", args)
    receipt = result["calculation"]
    assert result["progress"] and receipt["id"] == "c1" and receipt["result"] == "20"
    assert receipt["arithmetic_status"] == "computed" and receipt["verified"] is False
    repeated = execute(tool, "calculate", {**args, "label": "换个标题不算新计算"})
    assert not repeated["progress"] and len(tool.notebook["calculations"]) == 1
    assert evidence_context(tool.notebook)["calculations"] == [receipt]
    assert public_notebook(tool.notebook)["calculations"] == [receipt]
    report = append_investigation_report("# 原规则评级", tool.notebook)
    assert "20 %" in report and "原规则评级" in report
    assert "本期应收账款为120万元。" in report and "上期应收账款为100万元。" in report
    assert "不等于已核实事实" in report
    assert before == (tool.state["field_checks"], tool.state["risk_assessment"])
    tool.scout._analyze_search_results.assert_not_called()


def test_cited_passage_after_memory_prefix_survives_context_and_report():
    tail = "期后回款20万元仅对应另一客户，不能抵扣本案争议应收余额。"
    long_text = "背景材料" * 420 + "。\n" + tail
    tool, sid, _ = make_tools([{"chunk_index": 0, "content": long_text}])
    read = execute(tool, "read_source", {"source_id": sid})
    qid = next(k for k, text in read["quote_options"].items() if text == tail)
    execute(tool, "record_finding", {"claim": "期后回款主体与应收主体不同，不能直接相抵", "source_id": sid, "quote_id": qid,
                                    "kind": "counter"})
    context = evidence_context(tool.notebook)
    assert tail not in context["read_evidence"][0]["text"]
    assert context["read_evidence"][0]["truncated"]
    assert {"source_id": sid, "quote_id": qid, "quote": tail} in context["cited_evidence"]
    assert tail in append_investigation_report("原文", tool.notebook)


@pytest.mark.parametrize("action", ["search_web", "list_materials", "extract_evidence", "search_local"])
def test_disabled_tools_do_not_run(action):
    tool, sid, _ = make_tools()
    tool.state["search_local"] = False
    with pytest.raises(ValueError, match="未授权"):
        execute(tool, action, {"source_id": sid, "query": "企业回款"})
    tool.scout.milvus_service.get_document_chunks.assert_not_called()
    tool.scout._analyze_search_results.assert_not_called()


def test_scope_revocation_blocks_cached_read_citation_and_calculation():
    tool, sid, _ = make_tools()
    read = execute(tool, "read_source", {"source_id": sid})
    tool.state["kb_scope"] = []
    for action in ("read_source", "read_next", "extract_evidence"):
        with pytest.raises(ValueError, match="授权"):
            execute(tool, action, {"source_id": sid})
    rejected = execute(tool, "record_finding", {"claim": "撤销权限后不允许利用旧引文生成新结论", "source_id": sid, "quote_id": "q1"})
    assert not rejected["ok"] and not tool.notebook.get("findings")
    with pytest.raises(ValueError, match="citation could not be resolved"):
        execute(tool, "calculate", calculation_args(sid, read["quote_options"]))
    assert not tool.notebook.get("calculations")


@pytest.mark.parametrize("read_all", [False, True])
def test_recovery_tools_allow_remaining_reading_but_not_rereading_everything(read_all):
    tool, sid, _ = make_tools()
    execute(tool, "read_source", {"source_id": sid})
    if read_all:
        second = execute(tool, "read_next", {"source_id": sid})
        execute(tool, "read_next", {"source_id": second["source_id"]})
    contexts = []

    async def choose(_, context):
        contexts.append(copy.deepcopy(context))
        if len(contexts) < 3:
            return {"action": "inspect_checks", "arguments": {"observation_number": len(contexts)}}
        return {"action": "finish", "arguments": {"summary": "基于已读材料结束本轮，未完成核查保留限制。"}}

    result = asyncio.run(investigate(brief={"query": "调查"}, tools=tool.definitions(), choose=choose,
        execute=tool.execute, notebook=tool.notebook, critique=False,
        budget=InvestigationBudget(max_steps=4, max_seconds=5)))
    assert result["status"] == "completed" and contexts[2]["recovery"]["active"]
    assert ("read_next" in contexts[2]["tools"]) is (not read_all)
    assert ("read_source" in contexts[2]["tools"]) is (not read_all)
    assert "calculate" in contexts[2]["tools"] and "record_finding" in contexts[2]["tools"]
