# Copyright © 2026 XbhbxZty
"""Read-only access to the independently downloaded public dataset snapshot."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class LocalDataNotFound(LookupError):
    """A normalized record or local document was not found."""


class LocalDataRepository:
    """Serve normalized public data without PostgreSQL, Milvus, or source adapters."""

    def __init__(self, root: Optional[Path] = None) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        configured = os.getenv("LOCAL_DATA_ROOT")
        self.root = Path(root or configured or repository_root / "data" / "public_dd").resolve()
        self.normalized = self.root / "normalized"

    @staticmethod
    def _json(path: Path, default: Any = None) -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    @staticmethod
    def _normalized_text(value: Any) -> str:
        return re.sub(r"[\s_.（）()·-]", "", str(value or "")).lower()

    @staticmethod
    def _paginate(rows: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
        return {"items": rows[offset : offset + limit], "total": len(rows), "offset": offset, "limit": limit}

    @staticmethod
    def _matches(row: dict[str, Any], query: Optional[str], fields: Iterable[str]) -> bool:
        if not query:
            return True
        needle = LocalDataRepository._normalized_text(query)
        return any(needle in LocalDataRepository._normalized_text(row.get(field)) for field in fields)

    def snapshot(self) -> dict[str, Any]:
        snapshot = self._json(self.normalized / "snapshot.json", {}) or {}
        return {**snapshot, "status": "ready" if snapshot else "empty", "root": self.root.as_posix()}

    def datasets(self) -> list[dict[str, Any]]:
        return self._json(self.normalized / "datasets.json", []) or []

    def companies(
        self, *, query: Optional[str] = None, dataset_id: Optional[str] = None,
        market: Optional[str] = None, offset: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "companies.jsonl")
        if query:
            rows = [row for row in rows if self._matches(row, query, ("company_id", "legal_name", "company_code", "aliases"))]
        if dataset_id:
            rows = [row for row in rows if dataset_id in (row.get("source_datasets") or [])]
        if market:
            needle = self._normalized_text(market)
            rows = [row for row in rows if any(needle in self._normalized_text(value) for value in row.get("markets") or [])]
        return self._paginate(rows, offset, limit)

    def resolve_company(self, query: str) -> dict[str, Any]:
        result = self.companies(query=query, limit=1000)
        needle = self._normalized_text(query)
        exact = [
            row for row in result["items"]
            if needle in {
                self._normalized_text(row.get("company_id")),
                self._normalized_text(row.get("legal_name")),
                self._normalized_text(row.get("company_code")),
                *(self._normalized_text(value) for value in row.get("aliases") or []),
            }
        ]
        matches = exact or result["items"]
        state = "not_found" if not matches else "resolved" if len(matches) == 1 else "ambiguous"
        return {"query": query, "status": state, "matches": matches}

    def company(self, company_id: str) -> dict[str, Any]:
        if not _SAFE_ID.fullmatch(company_id):
            raise LocalDataNotFound(f"invalid company id: {company_id}")
        row = next((row for row in self._jsonl(self.normalized / "companies.jsonl") if row.get("company_id") == company_id), None)
        if not row:
            raise LocalDataNotFound(f"company not found: {company_id}")
        return {
            **row,
            "counts": {
                "documents": self.documents(company_id=company_id, limit=1)["total"],
                "tasks": self.tasks(company_id=company_id, limit=1)["total"],
                "financial_samples": self.financial_samples(company_id=company_id, limit=1)["total"],
                "research_structures": self.research_structures(company_id=company_id, limit=1)["total"],
            },
        }

    def documents(
        self, *, company_id: Optional[str] = None, dataset_id: Optional[str] = None,
        document_type: Optional[str] = None, local_only: bool = False,
        offset: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "documents.jsonl")
        for key, expected in {"company_id": company_id, "dataset_id": dataset_id, "document_type": document_type}.items():
            if expected:
                rows = [row for row in rows if row.get(key) == expected]
        if local_only:
            rows = [row for row in rows if row.get("available_locally")]
        for row in rows:
            row["file_url"] = f"/local-data/documents/{row['document_id']}/file" if row.get("available_locally") else None
        return self._paginate(rows, offset, limit)

    def financial_samples(self, *, company_id: Optional[str] = None, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "financial_samples.jsonl")
        if company_id:
            rows = [row for row in rows if row.get("company_id") == company_id]
        return self._paginate(rows, offset, limit)

    def tasks(
        self, *, company_id: Optional[str] = None, task_type: Optional[str] = None,
        query: Optional[str] = None, offset: int = 0, limit: int = 50,
    ) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "tasks.jsonl")
        if company_id:
            rows = [row for row in rows if row.get("company_id") == company_id]
        if task_type:
            rows = [row for row in rows if row.get("task_type") == task_type]
        if query:
            rows = [row for row in rows if self._matches(row, query, ("prompt", "ground_truth", "conditions"))]
        return self._paginate(rows, offset, limit)

    def legal_cases(self, *, query: Optional[str] = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "legal_cases.jsonl")
        if query:
            rows = [row for row in rows if self._matches(row, query, ("case_id", "description", "case_info", "insider_trading", "parties", "judgment"))]
        return self._paginate(rows, offset, limit)

    def research_structures(
        self, *, company_id: Optional[str] = None, query: Optional[str] = None,
        offset: int = 0, limit: int = 20,
    ) -> dict[str, Any]:
        rows = self._jsonl(self.normalized / "research_structures.jsonl")
        if company_id:
            rows = [row for row in rows if row.get("company_id") == company_id]
        if query:
            rows = [row for row in rows if self._matches(row, query, ("company_name", "market", "markdown"))]
        return self._paginate(rows, offset, limit)

    def search(self, query: str, *, domains: Optional[list[str]] = None, top_k: int = 20) -> list[dict[str, Any]]:
        domains = domains or ["company", "document", "task", "legal_case", "research_structure"]
        needle = self._normalized_text(query)
        hits: list[dict[str, Any]] = []
        sources = {
            "company": (self._jsonl(self.normalized / "companies.jsonl"), ("legal_name", "company_code", "aliases"), "company_id"),
            "document": (self._jsonl(self.normalized / "documents.jsonl"), ("title", "period", "document_type"), "document_id"),
            "task": (self._jsonl(self.normalized / "tasks.jsonl"), ("prompt", "ground_truth", "conditions"), "task_id"),
            "legal_case": (self._jsonl(self.normalized / "legal_cases.jsonl"), ("description", "case_info", "insider_trading", "parties", "judgment"), "case_id"),
            "research_structure": (self._jsonl(self.normalized / "research_structures.jsonl"), ("company_name", "markdown"), "structure_id"),
        }
        for domain in domains:
            if domain not in sources:
                continue
            rows, fields, id_field = sources[domain]
            for row in rows:
                score = sum(needle in self._normalized_text(row.get(field)) for field in fields)
                if score:
                    preview = next((str(row.get(field))[:500] for field in fields if needle in self._normalized_text(row.get(field))), "")
                    hits.append({"domain": domain, "record_id": row.get(id_field), "score": score, "preview": preview, "company_id": row.get("company_id")})
        hits.sort(key=lambda row: (-row["score"], row["domain"], str(row["record_id"])))
        return hits[:top_k]

    def document_path(self, document_id: str) -> tuple[Path, str]:
        if not _SAFE_ID.fullmatch(document_id):
            raise LocalDataNotFound(f"invalid document id: {document_id}")
        row = next((row for row in self._jsonl(self.normalized / "documents.jsonl") if row.get("document_id") == document_id), None)
        if not row or not row.get("available_locally") or not row.get("local_path"):
            raise LocalDataNotFound(f"local document not available: {document_id}")
        target = (self.root / str(row["local_path"])).resolve()
        if self.root != target and self.root not in target.parents:
            raise LocalDataNotFound(f"unsafe local path for document: {document_id}")
        if not target.is_file():
            raise LocalDataNotFound(f"local document missing: {document_id}")
        media_type = "application/pdf" if target.suffix.lower() == ".pdf" else "text/plain"
        return target, media_type
