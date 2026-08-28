"""Embed prepared real-case chunks into twelve sealed, case-scoped Milvus collections."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
sys.path.insert(0, os.fspath(APP))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
load_dotenv(BACKEND / ".env")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from real_case_rag import collection_for_case, validate_case_id  # noqa: E402


EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIMENSIONS = 1024
SUCCESS_ACQUISITION = {"downloaded", "existing"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _batches(values: list[Any], size: int) -> list[tuple[int, list[Any]]]:
    return [(start, values[start:start + size]) for start in range(0, len(values), size)]


def _embed_batch(texts: list[str], retries: int = 3) -> list[list[float]]:
    from service.embedding_service import generate_embedding

    last_error = "embedding returned empty or invalid vectors"
    for attempt in range(1, retries + 1):
        vectors = generate_embedding(
            texts,
            model_name=EMBEDDING_MODEL,
            dimensions=EMBEDDING_DIMENSIONS,
            max_batch_size=10,
        )
        if (
            isinstance(vectors, list)
            and len(vectors) == len(texts)
            and all(isinstance(vector, list) and len(vector) == EMBEDDING_DIMENSIONS
                    for vector in vectors)
        ):
            return vectors
        if attempt < retries:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(last_error)


def _embed_chunks(
    chunks: list[dict[str, Any]], workers: int, batch_size: int
) -> list[list[float]]:
    texts = [str(row["content"]) for row in chunks]
    vectors: list[list[float] | None] = [None] * len(texts)
    if not 1 <= batch_size <= 10:
        raise ValueError("embedding batch size must be between 1 and 10")
    work = _batches(texts, batch_size)
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_embed_batch, batch): (start, len(batch)) for start, batch in work}
        for future in as_completed(futures):
            start, length = futures[future]
            batch_vectors = future.result()
            vectors[start:start + length] = batch_vectors
            completed += length
            if completed % 200 < length or completed == len(texts):
                print(f"  embeddings: {completed}/{len(texts)}", flush=True)
    if any(vector is None for vector in vectors):
        raise RuntimeError("embedding output contains gaps")
    return [vector for vector in vectors if vector is not None]


def _validate_prepared_case(case_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    case_id = validate_case_id(case_dir.name)
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((case_dir / "validation_report.json").read_text(encoding="utf-8"))
    preparation = json.loads(
        (case_dir / "corpus" / "preparation_report.json").read_text(encoding="utf-8")
    )
    if validation.get("errors"):
        raise ValueError(f"{case_id} has structural validation errors")
    if manifest.get("label_policy", {}).get("reference_retrievable_by_agent") is not False:
        raise ValueError(f"{case_id} reference isolation policy is not sealed")
    if preparation.get("reference_or_post_cutoff_read") is not False:
        raise ValueError(f"{case_id} preparation reports answer-layer access")

    rag = {row["source_id"]: row for row in _jsonl(case_dir / "rag_manifest.jsonl")}
    acquired = {
        row["source_id"]: row
        for row in _jsonl(case_dir / "acquisition_manifest.jsonl")
        if row.get("status") in SUCCESS_ACQUISITION
    }
    documents = _jsonl(case_dir / "corpus" / "documents.jsonl")
    chunks = _jsonl(case_dir / "corpus" / "chunks.jsonl")
    document_ids = {row["document_id"] for row in documents if row.get("chunk_count", 0) > 0}

    if not chunks:
        raise ValueError(f"{case_id} has no prepared chunks")
    if len({row["id"] for row in chunks}) != len(chunks):
        raise ValueError(f"{case_id} contains duplicate chunk ids")
    for row in chunks:
        source_id = row.get("source_id")
        if source_id not in rag or source_id not in acquired:
            raise ValueError(f"{case_id}/{source_id} is not in the acquired RAG whitelist")
        rag_row = rag[source_id]
        if rag_row.get("document_role") != "primary_source":
            raise ValueError(f"{case_id}/{source_id} is not primary evidence")
        if rag_row.get("eligible_at_cutoff") is not True:
            raise ValueError(f"{case_id}/{source_id} is not cutoff eligible")
        if row.get("kb_id") != case_id or row.get("doc_id") not in document_ids:
            raise ValueError(f"{case_id}/{source_id} has invalid case/document identity")
        if "reference/" in row["content"] or "post_cutoff/" in row["content"]:
            raise ValueError(f"{case_id}/{source_id} contains an answer-layer path")
    return documents, chunks


def ingest_case(
    case_dir: Path,
    replace: bool,
    dry_run: bool,
    embedding_workers: int,
    embedding_batch_size: int,
) -> dict[str, Any]:
    case_id = validate_case_id(case_dir.name)
    collection_name = collection_for_case(case_id)
    documents, chunks = _validate_prepared_case(case_dir)
    nonempty_documents = [row for row in documents if row.get("chunk_count", 0) > 0]
    report: dict[str, Any] = {
        "case_id": case_id,
        "collection": collection_name,
        "status": "validated" if dry_run else "in_progress",
        "document_count": len(nonempty_documents),
        "empty_text_source_ids": [row["source_id"] for row in documents if not row.get("chunk_count")],
        "chunk_count": len(chunks),
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "case_ready": False,
        "reference_retrievable": False,
        "post_cutoff_retrievable": False,
    }
    if dry_run:
        return report

    from service.milvus_service import get_milvus_service

    milvus = get_milvus_service()
    exists = milvus.has_collection(collection_name)
    if exists and not replace:
        raise ValueError(f"collection already exists; pass --replace: {collection_name}")

    print(f"{case_id}: embedding {len(chunks)} chunks", flush=True)
    vectors = _embed_chunks(chunks, embedding_workers, embedding_batch_size)
    payload = [
        {
            "id": row["id"],
            "doc_id": row["doc_id"],
            "kb_id": case_id,
            "filename": row["filename"],
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "vector": vector,
        }
        for row, vector in zip(chunks, vectors)
    ]

    if exists:
        if not collection_name.startswith("eval_case_") or not collection_name.endswith("_sources"):
            raise ValueError(f"refusing to replace non-eval collection: {collection_name}")
        milvus.delete_collection(collection_name)
    inserted = 0
    for start in range(0, len(payload), 500):
        inserted += milvus.insert_documents(collection_name, payload[start:start + 500])
    stats = milvus.get_collection_stats(collection_name)
    actual = int(stats.get("num_entities") or 0)
    if inserted != len(payload) or actual != len(payload):
        raise RuntimeError(f"Milvus count mismatch: expected={len(payload)}, inserted={inserted}, actual={actual}")

    ingestion_rows = [
        {
            "case_id": case_id,
            "source_id": row["source_id"],
            "document_id": row["document_id"],
            "sha256": row["sha256"],
            "chunk_count": row["chunk_count"],
            "collection": collection_name,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "status": "completed",
        }
        for row in nonempty_documents
    ]
    _write_jsonl(case_dir / "corpus" / "ingestion_manifest.jsonl", ingestion_rows)
    report.update({
        "status": "completed",
        "inserted_chunk_count": inserted,
        "milvus_entity_count": actual,
        "case_ready": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    _write_json(case_dir / "corpus" / "ingestion_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed-root",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases_processed",
    )
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--embedding-workers", type=int, default=3)
    parser.add_argument("--embedding-batch-size", type=int, default=10)
    args = parser.parse_args()
    requested = set(args.case_ids or []) or None
    if requested:
        requested = {validate_case_id(case_id) for case_id in requested}
    case_dirs = sorted(path for path in args.processed_root.resolve().glob("case_*") if path.is_dir())
    if requested:
        case_dirs = [path for path in case_dirs if path.name in requested]

    reports: list[dict[str, Any]] = []
    failed = 0
    for case_dir in case_dirs:
        try:
            report = ingest_case(
                case_dir,
                replace=args.replace,
                dry_run=args.dry_run,
                embedding_workers=args.embedding_workers,
                embedding_batch_size=args.embedding_batch_size,
            )
        except Exception as exc:
            failed += 1
            report = {
                "case_id": case_dir.name,
                "status": "failed",
                "case_ready": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    summary = {
        "case_count": len(reports),
        "failed_count": failed,
        "dry_run": args.dry_run,
        "reports": reports,
    }
    _write_json(args.processed_root.resolve() / "ingestion_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
