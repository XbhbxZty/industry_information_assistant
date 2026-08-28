"""Fetch and hash the primary-source files queued by process_real_cases.py.

Downloads are deliberately stored below each processed case and never ingested into
the application collection automatically.  The resulting acquisition manifest can
be joined with rag_manifest.jsonl after document parsing and cutoff verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USER_AGENT = "DueDiligenceEvalCorpus/1.0 (+local research evaluation)"
CHUNK_SIZE = 1024 * 256


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
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _safe_target(case_dir: Path, relative_target: str) -> Path:
    """Resolve a queue target while preventing path traversal."""
    root = case_dir.resolve()
    target = (root / relative_target).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f"target escapes case directory: {relative_target}")
    return target


def _content_kind(prefix: bytes) -> str:
    stripped = prefix.lstrip()
    lowered = stripped[:4096].lower()
    if stripped.startswith(b"%PDF-"):
        return "pdf"
    if any(marker in lowered for marker in (b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html"
    return "binary"


def _validate_content(target: Path, prefix: bytes) -> tuple[bool, str]:
    kind = _content_kind(prefix)
    if target.suffix.lower() == ".pdf" and kind != "pdf":
        return False, f"expected_pdf_received_{kind}"
    if target.suffix.lower() in {".html", ".htm"} and kind == "binary":
        return False, "expected_html_received_binary"
    lowered = prefix.lower()
    if any(marker in lowered for marker in (
        b"access denied", b"captcha", b"verify you are human", b"cloudflare ray id"
    )):
        return False, "anti_bot_or_access_denied_page"
    return True, kind


def _existing_result(entry: dict[str, Any], target: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    with target.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            if not prefix:
                prefix = chunk[:8192]
            digest.update(chunk)
            size += len(chunk)
    valid, detail = _validate_content(target, prefix)
    return {
        **entry,
        "status": "existing" if valid else "invalid_existing",
        "validation": detail,
        "sha256": digest.hexdigest(),
        "bytes": size,
        "local_path": target.as_posix(),
    }


def _fetch_one(
    case_dir: Path,
    entry: dict[str, Any],
    timeout: float,
    max_bytes: int,
    overwrite: bool,
) -> dict[str, Any]:
    target = _safe_target(case_dir, str(entry["target_path"]))
    if target.exists() and not overwrite:
        return _existing_result(entry, target)

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(
        str(entry["url"]),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError(f"content_length_exceeds_limit:{declared}>{max_bytes}")
            with partial.open("wb") as handle:
                while chunk := response.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"download_exceeds_limit:{size}>{max_bytes}")
                    if not prefix:
                        prefix = chunk[:8192]
                    handle.write(chunk)
                    digest.update(chunk)
            content_type = response.headers.get_content_type()
            final_url = response.geturl()

        valid, validation = _validate_content(target, prefix)
        if not valid:
            partial.unlink(missing_ok=True)
            return {
                **entry,
                "status": "failed_validation",
                "validation": validation,
                "bytes": size,
                "http_content_type": content_type,
                "final_url": final_url,
            }
        os.replace(partial, target)
        return {
            **entry,
            "status": "downloaded",
            "validation": validation,
            "sha256": digest.hexdigest(),
            "bytes": size,
            "http_content_type": content_type,
            "final_url": final_url,
            "local_path": target.as_posix(),
        }
    except (OSError, ValueError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        return {
            **entry,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def fetch_all(
    output_dir: Path,
    case_ids: set[str] | None = None,
    workers: int = 4,
    timeout: float = 30,
    max_bytes: int = 50 * 1024 * 1024,
    overwrite: bool = False,
) -> dict[str, Any]:
    case_dirs = sorted(path for path in output_dir.glob("case_*") if path.is_dir())
    if case_ids:
        case_dirs = [path for path in case_dirs if path.name in case_ids]
    jobs: list[tuple[Path, dict[str, Any]]] = []
    for case_dir in case_dirs:
        for entry in _jsonl(case_dir / "download_queue.jsonl"):
            jobs.append((case_dir, entry))

    started = time.monotonic()
    results_by_case: dict[str, list[dict[str, Any]]] = {path.name: [] for path in case_dirs}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_fetch_one, case_dir, entry, timeout, max_bytes, overwrite): case_dir
            for case_dir, entry in jobs
        }
        for future in as_completed(futures):
            case_dir = futures[future]
            result = future.result()
            results_by_case[case_dir.name].append(result)
            results_by_case[case_dir.name].sort(key=lambda row: str(row.get("source_id")))
            _write_jsonl(case_dir / "acquisition_manifest.jsonl", results_by_case[case_dir.name])
            print(f"{case_dir.name}/{result.get('source_id')}: {result['status']}", flush=True)

    all_results = [row for rows in results_by_case.values() for row in rows]
    success_statuses = {"downloaded", "existing"}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(case_dirs),
        "queued_count": len(jobs),
        "successful_count": sum(row["status"] in success_statuses for row in all_results),
        "failed_count": sum(row["status"] not in success_statuses for row in all_results),
        "downloaded_bytes": sum(
            int(row.get("bytes") or 0) for row in all_results if row["status"] in success_statuses
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "status_counts": {
            status: sum(row["status"] == status for row in all_results)
            for status in sorted({row["status"] for row in all_results})
        },
    }
    _write_json(output_dir / "acquisition_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases_processed",
    )
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-mb", type=int, default=50)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    summary = fetch_all(
        args.output_dir.resolve(),
        set(args.case_ids or []) or None,
        workers=args.workers,
        timeout=args.timeout,
        max_bytes=args.max_mb * 1024 * 1024,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

