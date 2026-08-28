"""Normalize the downloaded public datasets behind one stable local schema."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("__MACOSX/") or "/._" in name or name.endswith("/.DS_Store"):
                continue
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe ZIP entry: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s\-_.（）()·]", "", value).lower()


def _company_id(name: str, code: str | None = None) -> str:
    if code:
        return code.strip().lower()
    digest = hashlib.sha1(_normalized_name(name).encode("utf-8")).hexdigest()[:12]
    return f"name-{digest}"


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def normalize(root: Path) -> dict[str, Any]:
    root = root.resolve()
    raw = root / "raw"
    extracted = root / "extracted"
    normalized = root / "normalized"
    acquisition = _json(root / "manifests" / "acquisition.json")

    _safe_extract(raw / "far" / "pdf_data.zip", extracted / "finar_bench")
    _safe_extract(raw / "far" / "pdf_extractor_result.zip", extracted / "finar_bench")

    companies: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    financial_samples: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    legal_cases: list[dict[str, Any]] = []
    structures: list[dict[str, Any]] = []

    def add_company(
        *, name: str, dataset_id: str, source_id: str, market: str | None = None,
        code: str | None = None,
    ) -> str:
        company_id = _company_id(name, code)
        row = companies.setdefault(
            company_id,
            {
                "company_id": company_id,
                "legal_name": name,
                "company_code": code,
                "markets": [],
                "aliases": [],
                "source_datasets": [],
                "source_records": [],
            },
        )
        if name not in row["aliases"]:
            row["aliases"].append(name)
        if market and market not in row["markets"]:
            row["markets"].append(market)
        if dataset_id not in row["source_datasets"]:
            row["source_datasets"].append(dataset_id)
        record = {"dataset_id": dataset_id, "source_id": source_id}
        if record not in row["source_records"]:
            row["source_records"].append(record)
        return company_id

    # FinDocResearch: metadata for 80 companies and two annual-report URLs each.
    with (raw / "fd" / "dataset.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source_id = row["Case ID"]
            name = row["Company Name"]
            company_id = add_company(
                name=name,
                dataset_id="findoc_research",
                source_id=source_id,
                market=row.get("Market"),
            )
            for year in ("23", "24"):
                filename = row.get(f"FY{year} File Name") or ""
                url = row.get(f"FY{year} PDF Download Site") or ""
                local = raw / "fd" / "reports" / filename
                documents.append(
                    {
                        "document_id": f"fd-{source_id}-20{year}",
                        "dataset_id": "findoc_research",
                        "company_id": company_id,
                        "document_type": "annual_report",
                        "period": f"FY20{year}",
                        "title": filename,
                        "remote_url": url,
                        "local_path": _relative(root, local) if local.is_file() else None,
                        "available_locally": local.is_file(),
                    }
                )

    # FinAR-Bench: 100 financial-statement samples and 1,300 graded tasks.
    for split in ("dev", "test"):
        for index, sample in enumerate(_jsonl(raw / "far" / f"{split}.txt"), start=1):
            instances = sample.get("instances") or []
            first = instances[0] if instances else {}
            code = str(first.get("company_code") or "").strip()
            name = str(first.get("company") or code)
            source_id = Path(str(sample.get("file_path") or code)).stem
            company_id = add_company(
                name=name,
                code=code or None,
                dataset_id="finar_bench",
                source_id=source_id,
                market="China A-share",
            )
            sample_id = f"far-{split}-{index:03d}"
            pdf = extracted / "finar_bench" / "pdf_data" / f"{source_id}.pdf"
            documents.append(
                {
                    "document_id": f"far-pdf-{source_id}",
                    "dataset_id": "finar_bench",
                    "company_id": company_id,
                    "document_type": "financial_statement_excerpt_pdf",
                    "period": "FY2023",
                    "title": f"{name} 2023 financial statement excerpt",
                    "remote_url": None,
                    "local_path": _relative(root, pdf) if pdf.is_file() else None,
                    "available_locally": pdf.is_file(),
                }
            )
            financial_samples.append(
                {
                    "sample_id": sample_id,
                    "dataset_id": "finar_bench",
                    "split": split,
                    "company_id": company_id,
                    "company_name": name,
                    "company_code": code,
                    "statement_markdown": sample.get("table") or "",
                    "task_count": len(instances),
                    "document_id": f"far-pdf-{source_id}",
                }
            )
            for instance in instances:
                tasks.append(
                    {
                        "task_id": instance.get("task_id"),
                        "dataset_id": "finar_bench",
                        "sample_id": sample_id,
                        "company_id": company_id,
                        "task_type": instance.get("task_type"),
                        "task_num": instance.get("task_num"),
                        "prompt": instance.get("task"),
                        "ground_truth": instance.get("ground_truth"),
                        "conditions": instance.get("conditions"),
                    }
                )

    # LawDual-Bench input descriptions plus the repository's structured gold labels.
    for row in _json(raw / "law" / "data" / "input_data.json"):
        sequence = int(row["序号"])
        annotation_path = raw / "law" / "data" / "processed" / f"entry_{sequence}.json"
        annotations = _json(annotation_path) if annotation_path.is_file() else None
        legal_cases.append(
            {
                "case_id": f"law-{sequence:04d}",
                "dataset_id": "lawdual_bench",
                "sequence": sequence,
                "description": row.get("案件描述") or "",
                "entity_linkage": "not_provided",
                "annotation_status": "available" if annotations else "missing",
                "case_info": (annotations or {}).get("案件信息"),
                "insider_trading": (annotations or {}).get("内幕交易信息的认定"),
                "parties": (annotations or {}).get("当事人信息"),
                "analysis": (annotations or {}).get("案件分析"),
                "judgment": (annotations or {}).get("最终判决"),
                "source_document_text": (annotations or {}).get("法律文书原文"),
                "annotation_source_path": _relative(root, annotation_path) if annotations else None,
            }
        )

    # FinDeepResearch: markdown analytical structures keyed by source case IDs.
    with (raw / "fdr" / "input_analytical_structure.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        for row in csv.DictReader(stream):
            source_id = row["Case ID"]
            name = row["Company Name"]
            company_id = add_company(
                name=name,
                dataset_id="findeep_research",
                source_id=source_id,
                market=row.get("Market"),
            )
            path = raw / "fdr" / "Analytical Structure" / f"{source_id}.md"
            structures.append(
                {
                    "structure_id": f"fdr-{source_id}",
                    "dataset_id": "findeep_research",
                    "company_id": company_id,
                    "company_name": name,
                    "market": row.get("Market"),
                    "markdown": path.read_text(encoding="utf-8") if path.is_file() else "",
                    "source_path": _relative(root, path) if path.is_file() else None,
                }
            )

    dataset_rows = []
    for item in acquisition["datasets"]:
        dataset_rows.append(
            {
                key: item[key]
                for key in (
                    "dataset_id", "repo_id", "revision", "license", "purpose",
                    "file_count", "total_bytes",
                )
            }
        )

    counts = {
        "datasets": len(dataset_rows),
        "companies": _write_jsonl(normalized / "companies.jsonl", sorted(companies.values(), key=lambda row: row["company_id"])),
        "documents": _write_jsonl(normalized / "documents.jsonl", documents),
        "financial_samples": _write_jsonl(normalized / "financial_samples.jsonl", financial_samples),
        "tasks": _write_jsonl(normalized / "tasks.jsonl", tasks),
        "legal_cases": _write_jsonl(normalized / "legal_cases.jsonl", legal_cases),
        "research_structures": _write_jsonl(normalized / "research_structures.jsonl", structures),
    }
    _write_json(normalized / "datasets.json", dataset_rows)

    digest = hashlib.sha256()
    for path in sorted(normalized.glob("*")):
        if path.is_file() and path.name != "snapshot.json":
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    content_sha256 = digest.hexdigest()
    snapshot = {
        "schema_version": "public-due-diligence-v1",
        "snapshot_id": f"public-dd-{content_sha256[:16]}",
        "content_sha256": content_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_policy": "external_public_downloads_only",
        "root": root.as_posix(),
        "counts": counts,
        "task_types": dict(sorted(Counter(row["task_type"] for row in tasks).items())),
        "licenses": sorted({row["license"] for row in dataset_rows}),
        "legal_annotations": dict(sorted(Counter(row["annotation_status"] for row in legal_cases).items())),
    }
    _write_json(normalized / "snapshot.json", snapshot)
    return snapshot


if __name__ == "__main__":
    repository_root = Path(__file__).resolve().parents[2]
    print(json.dumps(normalize(repository_root / "data" / "public_dd"), ensure_ascii=False, indent=2))
