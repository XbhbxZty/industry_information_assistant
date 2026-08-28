"""Verify every acquired file and the normalized snapshot digest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    checked = 0

    acquisition = json.loads((root / "manifests" / "acquisition.json").read_text(encoding="utf-8"))
    for dataset in acquisition["datasets"]:
        for row in dataset["files"]:
            path = root / row["path"]
            checked += 1
            if not path.is_file():
                errors.append(f"missing: {row['path']}")
            elif path.stat().st_size != row["bytes"]:
                errors.append(f"size mismatch: {row['path']}")
            elif _sha256(path) != row["sha256"]:
                errors.append(f"sha256 mismatch: {row['path']}")

    reports = json.loads((root / "manifests" / "findoc_reports.json").read_text(encoding="utf-8"))
    for row in reports["items"]:
        checked += 1
        path = Path(row["target_path"])
        if not path.is_file():
            errors.append(f"missing report: {path.name}")
        elif path.stat().st_size != row["bytes"]:
            errors.append(f"report size mismatch: {path.name}")
        elif _sha256(path) != row["sha256"]:
            errors.append(f"report sha256 mismatch: {path.name}")
        elif path.read_bytes()[:5] != b"%PDF-":
            errors.append(f"invalid PDF magic: {path.name}")

    normalized = root / "normalized"
    snapshot = json.loads((normalized / "snapshot.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for path in sorted(normalized.glob("*")):
        if path.is_file() and path.name != "snapshot.json":
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    if digest.hexdigest() != snapshot["content_sha256"]:
        errors.append("normalized snapshot digest mismatch")

    if "backend/eval" in root.as_posix().lower():
        errors.append("snapshot root is not isolated from backend/eval")

    return {
        "status": "ok" if not errors else "failed",
        "checked_files": checked,
        "repository_source_files": acquisition["file_count"],
        "report_files": len(reports["items"]),
        "snapshot_id": snapshot["snapshot_id"],
        "legal_annotations": snapshot.get("legal_annotations"),
        "errors": errors,
    }


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parents[2]
    result = verify(repository_root / "data" / "public_dd")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "ok" else 1)
