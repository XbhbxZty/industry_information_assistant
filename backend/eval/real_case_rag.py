"""Strict retrieval boundary for sealed real-case evaluation collections."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CASE_PATTERN = re.compile(r"case_(?:0[1-9]|1[0-2])\Z")


def validate_case_id(case_id: str) -> str:
    if not CASE_PATTERN.fullmatch(str(case_id)):
        raise ValueError(f"invalid sealed eval case id: {case_id!r}")
    return str(case_id)


def collection_for_case(case_id: str) -> str:
    return f"eval_{validate_case_id(case_id)}_sources"


def require_case_ready(case_id: str, processed_root: Path | None = None) -> dict[str, Any]:
    case_id = validate_case_id(case_id)
    root = processed_root or Path(__file__).resolve().parent / "real_cases_processed"
    path = root / case_id / "corpus" / "ingestion_report.json"
    if not path.exists():
        raise RuntimeError(f"sealed eval case has no completed ingestion report: {case_id}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("status") != "completed"
        or report.get("case_ready") is not True
        or report.get("collection") != collection_for_case(case_id)
        or report.get("reference_retrievable") is not False
        or report.get("post_cutoff_retrievable") is not False
    ):
        raise RuntimeError(f"sealed eval case is not ready: {case_id}")
    return report


def scope_for_case(case_id: str, processed_root: Path | None = None) -> list[dict[str, Any]]:
    """Build the only local-search scope accepted by the sealed eval runner."""
    report = require_case_ready(case_id, processed_root)
    case_id = validate_case_id(case_id)
    return [{
        "collection": collection_for_case(case_id),
        "kb_id": case_id,
        "kb_name": f"sealed-eval/{case_id}",
        "document_count": report.get("document_count", 0),
    }]


def retrieve_eval_case(case_id: str, question: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Retrieve one case; callers cannot provide paths, collections, or scopes."""
    case_id = validate_case_id(case_id)
    require_case_ready(case_id)
    if not question.strip():
        raise ValueError("question must not be empty")
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")

    from service.embedding_service import generate_embedding
    from service.milvus_service import get_milvus_service

    vector = generate_embedding(question)
    if not vector or len(vector) != 1024:
        raise RuntimeError("query embedding failed or returned the wrong dimension")
    hits = get_milvus_service().search(
        collection_for_case(case_id),
        vector,
        top_k=top_k,
        kb_id=case_id,
    )
    if any(hit.get("kb_id") != case_id for hit in hits):
        raise RuntimeError("cross-case retrieval detected")
    return hits
