"""Download pinned public due-diligence datasets into an isolated local snapshot.

This downloader deliberately does not read anything from ``backend/eval/real_cases``
or ``backend/eval/real_cases_processed``.  All raw files are acquired from the
public repositories listed in ``DATASETS`` and written below the requested root.

Run from the repository root::

    python backend/eval/download_public_datasets.py

The default destination is ``data/due_diligence_public``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The Xet transport can hang behind some Windows network filters.  Plain HTTP
# supports the same immutable revisions and resumes partial downloads reliably.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import snapshot_download


DATASETS: tuple[dict[str, Any], ...] = (
    {
        "dataset_id": "findoc_research",
        "storage_id": "fd",
        "repo_id": "OpenFinArena/FinDocResearch",
        "revision": "1ea4a6d41238cf8fc17ba6e85143710b4bbffd6b",
        "license": "apache-2.0",
        "allow_patterns": ["*.csv", "*.md", "*.pdf", "LICENSE*"],
        "purpose": "Company/year annual-report URL catalog and research examples",
    },
    {
        "dataset_id": "finar_bench",
        "storage_id": "far",
        "repo_id": "SAIFS-AiHub/FinAR-Bench",
        "revision": "a75026d0f2302432b7e52463865090f753d02bf6",
        "license": "apache-2.0",
        "allow_patterns": ["README*", "LICENSE*", "dev.txt", "test.txt", "*.zip"],
        "purpose": "Chinese annual-report financial-analysis benchmark",
    },
    {
        "dataset_id": "lawdual_bench",
        "storage_id": "law",
        "repo_id": "Yuwh07/LawDual-Bench",
        "revision": "1227ee5c07fdfdebcf15e0fc390fd0ecc4e12e67",
        "license": "apache-2.0",
        "allow_patterns": [
            "README*",
            "LICENSE*",
            "data/input_data.json",
            "data/extract_schema.json",
            "data/schema.json",
            "data/processed/*.json",
        ],
        "max_workers": 8,
        "purpose": "Chinese insider-trading legal-case extraction benchmark",
    },
    {
        "dataset_id": "findeep_research",
        "storage_id": "fdr",
        "repo_id": "OpenFinArena/FinDeepResearch",
        "revision": "d9f92f283e6a505d4db933d4d39a30814f7d512d",
        "license": "apache-2.0",
        "allow_patterns": ["*.csv", "*.md", "LICENSE*"],
        "purpose": "Financial deep-research analytical structures and report examples",
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def download(root: Path) -> dict[str, Any]:
    root = root.resolve()
    raw_root = root / "raw"
    cache_root = root / ".cache" / "huggingface"
    manifest_root = root / "manifests"
    raw_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    manifest_root.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()
    acquired: list[dict[str, Any]] = []
    for spec in DATASETS:
        # Keep physical names short enough for huggingface_hub's temporary
        # filenames under the legacy Windows MAX_PATH limit.
        destination = raw_root / spec["storage_id"]
        # Pre-create the metadata directory for stable Windows downloads.
        (destination / ".cache" / "huggingface" / "download").mkdir(
            parents=True, exist_ok=True
        )
        snapshot_download(
            repo_id=spec["repo_id"],
            repo_type="dataset",
            revision=spec["revision"],
            local_dir=destination,
            cache_dir=cache_root,
            allow_patterns=spec["allow_patterns"],
            max_workers=int(spec.get("max_workers", 1)),
        )
        files: list[dict[str, Any]] = []
        for path in sorted(destination.rglob("*")):
            if not path.is_file() or ".cache" in path.parts:
                continue
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        acquired.append(
            {
                **{key: value for key, value in spec.items() if key != "allow_patterns"},
                "allow_patterns": spec["allow_patterns"],
                "file_count": len(files),
                "total_bytes": sum(row["bytes"] for row in files),
                "files": files,
            }
        )

    completed_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "public-due-diligence-acquisition-v1",
        "source_policy": "external_public_downloads_only",
        "started_at": started_at,
        "completed_at": completed_at,
        "datasets": acquired,
        "dataset_count": len(acquired),
        "file_count": sum(row["file_count"] for row in acquired),
        "total_bytes": sum(row["total_bytes"] for row in acquired),
    }
    _write_json(manifest_root / "acquisition.json", manifest)
    return manifest


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=repository_root / "data" / "public_dd",
    )
    args = parser.parse_args()
    manifest = download(args.root)
    print(
        json.dumps(
            {
                "root": os.fspath(args.root.resolve()),
                "dataset_count": manifest["dataset_count"],
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
