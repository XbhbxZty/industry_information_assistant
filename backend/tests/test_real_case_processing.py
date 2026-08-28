# Copyright © 2026 XbhbxZty
"""Regression tests for the public-data due-diligence case normalizer."""

import json
import os
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND))

from eval import process_real_cases  # noqa: E402
from eval import fetch_real_case_sources  # noqa: E402
from eval import real_case_rag  # noqa: E402
from eval import score_real_case_run  # noqa: E402


CASES = BACKEND / "eval" / "real_cases"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _process(tmp_path: Path) -> tuple[dict, Path]:
    output = tmp_path / "processed"
    result = process_real_cases.process_all(CASES, output)
    return result, output


def test_all_twelve_cases_normalize_without_integrity_errors(tmp_path):
    index, output = _process(tmp_path)

    assert index["case_count"] == 12
    assert index["failed_count"] == 0
    assert sum(case["metrics"]["normalized_claim_rows"] for case in index["cases"]) == 731
    assert sum(case["metrics"]["normalized_source_rows"] for case in index["cases"]) == 162
    assert sum(case["metrics"]["checklist_rows"] for case in index["cases"]) == 364
    assert sum(case["metrics"]["post_cutoff_claim_rows"] for case in index["cases"]) == 10

    for case in index["cases"]:
        case_dir = output / case["case_id"]
        manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (case_dir / "validation_report.json").read_text(encoding="utf-8")
        )
        sources = _jsonl(case_dir / "sources.jsonl")
        reference_claims = _jsonl(case_dir / "reference" / "claims.jsonl")
        post_cutoff_claims = _jsonl(case_dir / "post_cutoff" / "claims.jsonl")
        checklist = json.loads(
            (case_dir / "reference" / "checklist.json").read_text(encoding="utf-8")
        )
        assessment = json.loads(
            (case_dir / "reference" / "assessment.json").read_text(encoding="utf-8")
        )

        source_ids = {source["source_id"] for source in sources}
        claim_ids = {
            claim["claim_id"] for claim in [*reference_claims, *post_cutoff_claims]
        }
        assert not validation["errors"]
        assert len(source_ids) == len(sources)
        assert len(claim_ids) == len(reference_claims) + len(post_cutoff_claims)
        assert all(set(claim.get("source_ids", [])) <= source_ids for claim in reference_claims)
        assert all(set(claim.get("source_ids", [])) <= source_ids for claim in post_cutoff_claims)
        assert all(
            set(item.get("evidence_ids", [])) <= source_ids | claim_ids for item in checklist
        )
        assert all("post_cutoff" not in str(claim.get("category", "")).lower()
                   for claim in reference_claims)
        assert manifest["label_policy"]["grade"] == "silver"
        assert manifest["label_policy"]["reference_retrievable_by_agent"] is False
        assert assessment["eval_only"] is True
        assert assessment["human_review_status"] == "not_reviewed"


def test_rag_manifest_contains_only_cutoff_eligible_primary_sources(tmp_path):
    index, output = _process(tmp_path)

    for case in index["cases"]:
        rag_rows = _jsonl(output / case["case_id"] / "rag_manifest.jsonl")
        assert all(row["document_role"] == "primary_source" for row in rag_rows)
        assert all(row["eligible_at_cutoff"] is True for row in rag_rows)
        assert all(row["access_status_class"] != "failed" for row in rag_rows)
        assert all(row["ready_for_ingest"] is False for row in rag_rows)


def test_known_truncation_and_cutoff_edge_cases_are_preserved(tmp_path):
    _, output = _process(tmp_path)

    expected_counts = {
        "case_01": (58, 0),
        "case_02": (68, 0),
        "case_04": (64, 3),
        "case_05": (83, 0),
        "case_06": (58, 3),
        "case_07": (40, 2),
        "case_12": (54, 2),
    }
    for case_id, (reference_count, post_count) in expected_counts.items():
        assert len(_jsonl(output / case_id / "reference" / "claims.jsonl")) == reference_count
        assert len(_jsonl(output / case_id / "post_cutoff" / "claims.jsonl")) == post_count

    # These are cutoff-date evidence gaps, despite being tagged as_of_eligible=false
    # in the generated pack. They must remain reference inputs, not future outcomes.
    case_05_reference = {
        claim["claim_id"]
        for claim in _jsonl(output / "case_05" / "reference" / "claims.jsonl")
    }
    assert {f"C{number:03d}" for number in range(78, 84)} <= case_05_reference

    # The compact JSON in case_01 omits C015; the complete Markdown table restores it.
    case_01_reference = {
        claim["claim_id"]
        for claim in _jsonl(output / "case_01" / "reference" / "claims.jsonl")
    }
    assert "C015" in case_01_reference


def test_source_fetcher_rejects_path_traversal_and_wrong_pdf_content(tmp_path):
    case_dir = tmp_path / "case_01"
    case_dir.mkdir()

    safe = fetch_real_case_sources._safe_target(case_dir, "raw_sources/S01.pdf")
    assert case_dir.resolve() in safe.parents
    try:
        fetch_real_case_sources._safe_target(case_dir, "../outside.pdf")
    except ValueError:
        pass
    else:
        raise AssertionError("download targets must not escape their case directory")

    assert fetch_real_case_sources._validate_content(safe, b"%PDF-1.7\n")[0] is True
    valid, reason = fetch_real_case_sources._validate_content(
        safe, b"<!doctype html><html><body>redirect</body></html>"
    )
    assert valid is False
    assert reason == "expected_pdf_received_html"


def test_eval_retrieval_collection_is_derived_from_a_strict_case_id():
    assert real_case_rag.collection_for_case("case_01") == "eval_case_01_sources"
    assert real_case_rag.collection_for_case("case_12") == "eval_case_12_sources"
    for unsafe in ("case_00", "case_13", "case_01/../reference", "eval_case_01_sources"):
        try:
            real_case_rag.collection_for_case(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe case id was accepted: {unsafe}")


def test_run_scorer_distinguishes_local_retrieval_from_presented_sources():
    events = [
        {
            "type": "search_results",
            "content": {
                "searchType": "local",
                "results": [{
                    "url": "local://kb/case_01/doc-a",
                    "snippet": "[case_id=case_01; source_id=S001; locator=page:1] text",
                }],
            },
        },
        {
            "type": "search_results",
            "content": {"results": [{"url": "https://example.test/source.pdf"}]},
        },
    ]
    audit = score_real_case_run._search_audit(events, "case_01")
    assert audit["retrieved_result_count"] == 1
    assert audit["all_result_urls_case_local"] is True
    assert audit["all_snippets_case_tagged"] is True


def test_run_scorer_detects_dates_after_research_cutoff():
    report = "年报发布于2025-03-15；错误引用生成于2026年8月9日。"
    assert score_real_case_run._post_cutoff_dates(report, "2025-05-31") == ["2026-08-09"]


def test_sealed_appendix_source_id_counts_as_a_citation():
    report = "原始证据来源：\n\n- [S002] S002.pdf；page:18；local://kb/case_01/doc"
    source = {
        "source_id": "S002",
        "title": "宁德时代新能源科技股份有限公司2024年年度报告全文",
        "url": "https://example.invalid/annual-report.pdf",
    }

    assert score_real_case_run._source_is_cited(
        report, score_real_case_run._compact(report), source
    )


def test_bare_source_id_does_not_count_as_a_citation():
    report = "内部调试字段 source_id=S002"
    source = {
        "source_id": "S002",
        "title": "未出现的标题",
        "url": "https://unused.invalid",
    }

    assert not score_real_case_run._source_is_cited(
        report, score_real_case_run._compact(report), source
    )


def test_claim_variants_accept_exact_thousand_yuan_to_wanyuan_conversion():
    claim = {"value": 362012554, "value_raw": "362012554", "unit": "千元"}

    assert score_real_case_run._compact("36201255.4万元") in (
        score_real_case_run._claim_variants(claim)
    )


def _synthetic_run(tmp_path: Path, *, section_failures: list[dict]) -> Path:
    """造一个最小可计分的运行目录，只改章节故障这一个变量。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    final_event = {
        "type": "research_complete",
        "final_report": "报告正文" * 300,
        "risk_assessment": {"requires_human_review": True},
        "errors": [],
        "section_failures": section_failures,
    }
    (run_dir / "result.json").write_text(json.dumps({
        "case_id": "case_01",
        "session_id": "unit-test",
        "status": "completed",
        "research_cutoff": "2025-05-31",
        "search_web": False,
        "search_local": True,
        "kb_scope": [{"kb_id": "case_01"}],
        "reference_layer_read": False,
        "post_cutoff_layer_read": False,
        "final_event": final_event,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.md").write_text(final_event["final_report"], encoding="utf-8")
    (run_dir / "events.jsonl").write_text(json.dumps({
        "type": "search_results", "as_of": "2025-05-31",
        "content": {"searchType": "local", "results": [{
            "url": "local://kb/case_01/doc-a",
            "snippet": "[case_id=case_01; source_id=S001; locator=page:1] 文本",
        }]},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return run_dir


def test_section_extraction_failure_makes_a_run_invalid_not_merely_failed(tmp_path):
    """章节抽取失败的运行不可用于评价模型（BC-56）。

    崩掉的章节证据为空，与"该章确实没有材料"在指标上完全一致。把这种运行
    算进模型对比，会把一次 AttributeError 记成"这个模型覆盖率低"——正是
    BC-55 那一轮发生过的事。所以它必须比 fail 更醒目：
    fail 是"跑完了但没达标"，invalid 是"这一轮不算数"。
    """
    failed = score_real_case_run.score_run(_synthetic_run(tmp_path, section_failures=[
        {"section_id": "sec_4", "section_title": "财务分析",
         "failure_kind": "llm_timeout", "failure_reason": "超过 90 秒上界"},
    ]))
    assert failed["verdict"] == "invalid"
    assert failed["gates"]["no_section_extraction_failures"] is False
    assert failed["metrics"]["section_failure_count"] == 1


def test_clean_run_is_scored_normally_and_never_marked_invalid(tmp_path):
    """互为反面：没有章节故障时，判定必须回到正常的 pass/fail 轨道。"""
    clean = score_real_case_run.score_run(_synthetic_run(tmp_path, section_failures=[]))
    assert clean["verdict"] in {"pass", "fail"}
    assert clean["gates"]["no_section_extraction_failures"] is True
    assert clean["metrics"]["section_failure_count"] == 0


def test_every_case_01_decision_claim_maps_to_a_checklist_field(tmp_path):
    """BC-58 的验收条件：未映射主张数必须为 0。

    改造前：29 条决策参考主张里只有 2 条（都是 revenue）的字段名落在系统
    清单内，其余 27 条系统 schema 根本表达不了。那种状态下谈"模型覆盖率
    只有 22%"是错误归因——模型不可能产出一个不存在的字段。

    这一条**先于**覆盖率成立：schema 能表达，才轮到评价抽取能力。
    """
    score = score_real_case_run.score_run(_synthetic_run(tmp_path, section_failures=[]))
    assert score["metrics"]["unmapped_reference_claims"] == 0, (
        f"仍有主张无法映射到核心或场景字段：{score['unmapped_claim_ids']}"
    )
    assert score["gates"]["all_reference_claims_mappable"] is True


def test_scorer_reports_core_and_scenario_coverage_separately(tmp_path):
    """双覆盖率：合成一个数会同时失去两个意义。

    核心覆盖率问"主体查清了吗"，场景覆盖率问"这笔业务的决策变量齐了吗"。
    """
    score = score_real_case_run.score_run(_synthetic_run(tmp_path, section_failures=[]))
    scopes = score["coverage_by_scope"]
    assert set(scopes) <= {"core", "scenario", "unmapped"}
    assert scopes["core"]["total"] > 0 and scopes["scenario"]["total"] > 0, \
        "case_01 同时包含主体类与保理场景类主张，两个桶都应非空"
    assert scopes["core"]["total"] + scopes["scenario"]["total"] == \
        score["metrics"]["scoreable_reference_claims"]
    assert "core_claim_coverage" in score["metrics"]
    assert "scenario_claim_coverage" in score["metrics"]


def test_scorer_claim_mapping_cannot_silently_drift_from_the_checklist():
    """映射表的目标字段必须都真实存在。

    清单改名后若忘了同步这张表，被改名的字段会静默变成"未映射"，
    于是一处能力**存在**的字段被报成覆盖缺口——BC-59 的假阴性形态。
    模块导入期就该炸掉，而不是在跑分时给出一个错误的低分。
    """
    targets = set(score_real_case_run._CLAIM_FIELD_TO_CHECKLIST.values())
    unknown = targets - set(score_real_case_run.CHECKLIST_BY_ID)
    assert not unknown, f"映射到了不存在的清单字段：{sorted(unknown)}"
