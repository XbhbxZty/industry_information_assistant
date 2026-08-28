"""Verify sealed real-case Milvus collections against the prepared chunk corpus."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
load_dotenv(BACKEND / ".env")

from real_case_rag import collection_for_case  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def verify(processed_root: Path) -> dict[str, Any]:
    from service.milvus_service import get_milvus_service

    milvus = get_milvus_service()
    collections = set(milvus.list_collections())
    reports: list[dict[str, Any]] = []
    for number in range(1, 13):
        case_id = f"case_{number:02d}"
        case_dir = processed_root / case_id
        collection = collection_for_case(case_id)
        expected_chunks = _jsonl(case_dir / "corpus" / "chunks.jsonl")
        expected_ids = {row["id"] for row in expected_chunks}
        expected_doc_ids = {row["doc_id"] for row in expected_chunks}
        errors: list[str] = []
        actual_rows: list[dict[str, Any]] = []
        if collection not in collections:
            errors.append("collection_missing")
        else:
            actual_rows = milvus.iter_all_rows(collection, include_vector=False)
            actual_ids = {row["id"] for row in actual_rows}
            if actual_ids != expected_ids:
                errors.append(
                    f"chunk_id_set_mismatch:expected={len(expected_ids)},actual={len(actual_ids)}"
                )
            if any(row.get("kb_id") != case_id for row in actual_rows):
                errors.append("cross_case_kb_id")
            if any(row.get("doc_id") not in expected_doc_ids for row in actual_rows):
                errors.append("unknown_document_id")
            expected_marker = f"[case_id={case_id};"
            if any(expected_marker not in str(row.get("content") or "") for row in actual_rows):
                errors.append("missing_or_wrong_case_marker")
            if any(
                "reference/" in str(row.get("content") or "")
                or "post_cutoff/" in str(row.get("content") or "")
                for row in actual_rows
            ):
                errors.append("answer_layer_path_leakage")

        reports.append({
            "case_id": case_id,
            "collection": collection,
            "expected_chunk_count": len(expected_chunks),
            "actual_chunk_count": len(actual_rows),
            "ready": not errors,
            "errors": errors,
        })

    summary = {
        "case_count": 12,
        "ready_case_count": sum(row["ready"] for row in reports),
        "failed_case_count": sum(not row["ready"] for row in reports),
        "expected_chunk_count": sum(row["expected_chunk_count"] for row in reports),
        "actual_chunk_count": sum(row["actual_chunk_count"] for row in reports),
        "cross_case_leakage_detected": any(
            "cross_case_kb_id" in row["errors"] or "missing_or_wrong_case_marker" in row["errors"]
            for row in reports
        ),
        "answer_layer_leakage_detected": any(
            "answer_layer_path_leakage" in row["errors"] for row in reports
        ),
        "reports": reports,
    }
    _write_json(processed_root / "rag_verification_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed-root",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases_processed",
    )
    args = parser.parse_args()
    summary = verify(args.processed_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

