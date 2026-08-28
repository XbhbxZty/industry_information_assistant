"""Run one real semantic retrieval against each sealed evaluation case."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parent / "real_cases_processed"
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
load_dotenv(BACKEND / ".env")

from real_case_rag import retrieve_eval_case  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    reports: list[dict[str, Any]] = []
    for number in range(1, 13):
        case_id = f"case_{number:02d}"
        case_dir = ROOT / case_id
        manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        subject_data = manifest.get("subject", {})
        subject = subject_data.get("name") or subject_data.get("legal_name") or case_id
        allowed_sources = {
            row["source_id"] for row in _jsonl(case_dir / "rag_manifest.jsonl")
        }
        query = f"{subject}的主营业务、财务状况和主要风险是什么？"
        try:
            hits = retrieve_eval_case(case_id, query, top_k=3)
            source_ids: list[str] = []
            errors: list[str] = []
            for hit in hits:
                match = None
                content = str(hit.get("content") or "")
                import re
                match = re.search(r"source_id=([^;\]]+)", content)
                source_id = match.group(1) if match else ""
                source_ids.append(source_id)
                if hit.get("kb_id") != case_id:
                    errors.append("cross_case_kb_id")
                if source_id not in allowed_sources:
                    errors.append(f"source_not_whitelisted:{source_id}")
                if f"[case_id={case_id};" not in content:
                    errors.append("wrong_case_marker")
            if not hits:
                errors.append("no_hits")
            reports.append({
                "case_id": case_id,
                "subject": subject,
                "query": query,
                "hit_count": len(hits),
                "source_ids": source_ids,
                "top_score": hits[0]["score"] if hits else None,
                "passed": not errors,
                "errors": errors,
            })
        except Exception as exc:
            reports.append({
                "case_id": case_id,
                "subject": subject,
                "query": query,
                "hit_count": 0,
                "passed": False,
                "errors": [f"{type(exc).__name__}:{exc}"],
            })
    summary = {
        "case_count": 12,
        "passed_count": sum(row["passed"] for row in reports),
        "failed_count": sum(not row["passed"] for row in reports),
        "reports": reports,
    }
    (ROOT / "retrieval_smoke_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
