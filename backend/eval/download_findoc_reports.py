"""Download and verify the China annual reports referenced by FinDocResearch."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALTERNATE_URLS = {
    ("test022", "2023"): "https://video.ceultimate.com/100009_2012105017/%E4%B8%87%E5%8D%8E%E5%8C%96%E5%AD%A6%E5%B9%B4%E6%8A%A5-2023--03.pdf",
    ("test022", "2024"): "https://static.cninfo.com.cn/finalpage/2025-04-15/1223097325.PDF",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as stream:
        return stream.read(5) == b"%PDF-"


def _download_one(item: dict[str, str], target_dir: Path, timeout: float, max_bytes: int) -> dict[str, Any]:
    filename = Path(item["filename"]).name
    target = target_dir / filename
    base = {
        "case_id": item["case_id"],
        "company_name": item["company_name"],
        "fiscal_year": item["fiscal_year"],
        "source_url": item["url"],
        "alternate_url": item.get("alternate_url"),
        "target_path": target.as_posix(),
    }
    if _is_pdf(target):
        return {
            **base,
            "status": "existing",
            "bytes": target.stat().st_size,
            "sha256": _sha256(target),
        }

    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        item["url"],
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PublicDueDiligenceDataset/1.0)",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
        },
    )
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            content_type = response.headers.get("Content-Type", "")
            total = 0
            with temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError(f"file exceeds {max_bytes} bytes")
                    output.write(block)
            with temporary.open("rb") as stream:
                compressed = stream.read(2) == b"\x1f\x8b"
            if compressed:
                decoded = temporary.with_suffix(temporary.suffix + ".decoded")
                with gzip.open(temporary, "rb") as source, decoded.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                os.replace(decoded, temporary)
            if not _is_pdf(temporary):
                prefix = temporary.read_bytes()[:80]
                raise ValueError(f"response is not a PDF: {prefix!r}")
            os.replace(temporary, target)
            return {
                **base,
                "status": "downloaded",
                "final_url": response.geturl(),
                "content_type": content_type,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
    except Exception as exc:  # retry via the system TLS stack before recording failure
        if temporary.exists():
            temporary.unlink()
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl:
            result = subprocess.run(
                [
                    curl,
                    "--fail",
                    "--location",
                    "--compressed",
                    "--http1.1",
                    "--retry",
                    "2",
                    "--connect-timeout",
                    str(max(10, int(timeout // 2))),
                    "--max-time",
                    str(max(60, int(timeout * 3))),
                    "--user-agent",
                    "Mozilla/5.0 (compatible; PublicDueDiligenceDataset/1.0)",
                    "--output",
                    os.fspath(temporary),
                    item.get("alternate_url") or item["url"],
                ],
                capture_output=True,
                text=True,
                timeout=max(90, timeout * 4),
                check=False,
            )
            if result.returncode == 0 and _is_pdf(temporary) and temporary.stat().st_size <= max_bytes:
                os.replace(temporary, target)
                return {
                    **base,
                    "status": "downloaded",
                    "transport": "curl-http1.1-fallback",
                    "bytes": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            curl_error = result.stderr.strip()[-500:]
        else:
            curl_error = "curl is unavailable"
        if temporary.exists():
            temporary.unlink()
        return {
            **base,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}; fallback: {curl_error}",
        }


def download(root: Path, workers: int = 4, timeout: float = 90, max_mb: int = 300) -> dict[str, Any]:
    root = root.resolve()
    source = root / "raw" / "fd" / "dataset.csv"
    destination = root / "raw" / "fd" / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, str]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("Market") != "China":
                continue
            for suffix in ("23", "24"):
                items.append(
                    {
                        "case_id": row["Case ID"],
                        "company_name": row["Company Name"],
                        "fiscal_year": f"20{suffix}",
                        "filename": row[f"FY{suffix} File Name"],
                        "url": row[f"FY{suffix} PDF Download Site"],
                        "alternate_url": ALTERNATE_URLS.get((row["Case ID"], f"20{suffix}"), ""),
                    }
                )

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_download_one, item, destination, timeout, max_mb * 1024 * 1024): item
            for item in items
        }
        for future in as_completed(futures):
            result = future.result()
            rows.append(result)
            print(json.dumps({key: result.get(key) for key in ("case_id", "fiscal_year", "status", "bytes", "error") if result.get(key) is not None}, ensure_ascii=False), flush=True)

    rows.sort(key=lambda row: (row["case_id"], row["fiscal_year"]))
    summary = {
        "schema_version": "findoc-report-acquisition-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested": len(rows),
        "downloaded": sum(row["status"] == "downloaded" for row in rows),
        "existing": sum(row["status"] == "existing" for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "total_bytes": sum(int(row.get("bytes") or 0) for row in rows),
        "items": rows,
    }
    manifest = root / "manifests" / "findoc_reports.json"
    manifest.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root / "data" / "public_dd")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--max-mb", type=int, default=300)
    arguments = parser.parse_args()
    result = download(arguments.root, arguments.workers, arguments.timeout, arguments.max_mb)
    print(json.dumps({key: result[key] for key in ("requested", "downloaded", "existing", "failed", "total_bytes")}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["failed"] else 0)
