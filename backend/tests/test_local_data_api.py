# Copyright © 2026 XbhbxZty
"""Regression tests for the unified public-data API boundary."""

import json
import os
import sys
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, os.fspath(APP))

from service.local_data_repository import LocalDataNotFound, LocalDataRepository  # noqa: E402


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> LocalDataRepository:
    root = tmp_path / "public_dd"
    normalized = root / "normalized"
    pdf = root / "raw" / "sample.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    _write_json(normalized / "snapshot.json", {
        "snapshot_id": "public-dd-test", "source_policy": "external_public_downloads_only",
        "counts": {"datasets": 2, "companies": 1, "documents": 1, "tasks": 1, "legal_cases": 1},
    })
    _write_json(normalized / "datasets.json", [
        {"dataset_id": "finar_bench"}, {"dataset_id": "lawdual_bench"},
    ])
    _write_jsonl(normalized / "companies.jsonl", [{
        "company_id": "603421.sh", "legal_name": "鼎信通讯", "company_code": "603421.SH",
        "aliases": ["鼎信通讯"], "markets": ["China A-share"], "source_datasets": ["finar_bench"],
    }])
    _write_jsonl(normalized / "documents.jsonl", [{
        "document_id": "far-pdf-603421", "dataset_id": "finar_bench", "company_id": "603421.sh",
        "document_type": "financial_statement_excerpt_pdf", "title": "鼎信通讯 2023",
        "available_locally": True, "local_path": "raw/sample.pdf",
    }])
    _write_jsonl(normalized / "financial_samples.jsonl", [{
        "sample_id": "far-dev-001", "company_id": "603421.sh", "statement_markdown": "|营业收入|1|",
    }])
    _write_jsonl(normalized / "tasks.jsonl", [{
        "task_id": "task-1", "company_id": "603421.sh", "task_type": "fact",
        "prompt": "提取营业收入", "ground_truth": "1",
    }])
    _write_jsonl(normalized / "legal_cases.jsonl", [{
        "case_id": "law-0001", "description": "内幕交易处罚案例",
    }])
    _write_jsonl(normalized / "research_structures.jsonl", [])
    return LocalDataRepository(root)


def test_default_root_is_isolated_from_repository_evaluation_data():
    repository = LocalDataRepository()
    assert repository.root.name == "public_dd"
    assert "backend/eval" not in repository.root.as_posix().lower()


def test_snapshot_company_resolution_and_domain_counts(tmp_path):
    repository = _fixture(tmp_path)
    assert repository.snapshot()["status"] == "ready"
    assert len(repository.datasets()) == 2
    resolved = repository.resolve_company("603421")
    assert resolved["status"] == "resolved"
    company = repository.company("603421.sh")
    assert company["counts"] == {
        "documents": 1, "tasks": 1, "financial_samples": 1, "research_structures": 0,
    }


def test_filters_search_and_document_provenance(tmp_path):
    repository = _fixture(tmp_path)
    assert repository.tasks(task_type="fact", query="营业收入")["total"] == 1
    assert repository.legal_cases(query="内幕交易")["total"] == 1
    assert repository.search("营业收入", domains=["task"])[0]["record_id"] == "task-1"
    document = repository.documents(local_only=True)["items"][0]
    assert document["file_url"] == "/local-data/documents/far-pdf-603421/file"
    path, media_type = repository.document_path("far-pdf-603421")
    assert path.is_file()
    assert media_type == "application/pdf"


def test_invalid_identifiers_cannot_escape_the_public_root(tmp_path):
    repository = _fixture(tmp_path)
    for unsafe in ("../sample", "x/../../", "C:\\Windows"):
        try:
            repository.document_path(unsafe)
        except LocalDataNotFound:
            pass
        else:
            raise AssertionError(f"unsafe identifier accepted: {unsafe}")


def test_router_exposes_all_sources_through_one_api(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from router import local_data_router

    repository = _fixture(tmp_path)
    app = FastAPI()
    app.include_router(local_data_router.router)
    app.dependency_overrides[local_data_router.get_local_data_repository] = lambda: repository
    client = TestClient(app)

    assert client.get("/local-data/health").json()["snapshot"]["snapshot_id"] == "public-dd-test"
    assert client.get("/local-data/datasets").json()["total"] == 2
    assert client.get("/local-data/companies/resolve", params={"query": "鼎信通讯"}).json()["status"] == "resolved"
    assert client.get("/local-data/tasks", params={"task_type": "fact"}).json()["total"] == 1
    assert client.get("/local-data/legal-cases", params={"query": "内幕交易"}).json()["total"] == 1
    assert client.get("/local-data/documents/far-pdf-603421/file").status_code == 200
