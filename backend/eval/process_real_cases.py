"""Normalize public-data due-diligence case packs into an isolated eval corpus.

The source case packs intentionally contain both evidence manifests and silver-label
answers.  They must never be ingested wholesale into the application RAG collection.
This script keeps the original Markdown untouched and emits separate artifacts for:

* source acquisition and later RAG ingestion;
* normalized silver-label claims/checklists/assessments;
* post-cutoff outcomes;
* validation findings.

Only Python's standard library is used so the processor can run in CI before the
backend's optional AI dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "real-case-v1"
CASE_GLOB = "case_*.md"


SOURCE_HEADER_ALIASES = {
    "source_id": "source_id",
    "来源标题": "title",
    "发布机构": "publisher",
    "发布机构/载体": "publisher",
    "来源等级": "source_level",
    "等级": "source_level",
    "发布日期": "publication_date",
    "事件日期": "event_date",
    "事件/数据日期": "event_date",
    "直接url": "direct_url",
    "直接URL": "direct_url",
    "直接 URL": "direct_url",
    "格式": "file_type",
    "文件格式": "file_type",
    "页码/章节": "location",
    "访问状态": "access_status",
    "可下载": "downloadable",
    "是否可下载": "downloadable",
    "支持项目": "supports",
    "支持的调查项目": "supports",
}

CHECKLIST_HEADER_ALIASES = {
    "检查项目": "item",
    "状态": "status",
    "结论": "conclusion",
    "证据ID": "evidence_ids",
    "证据id": "evidence_ids",
    "冲突": "conflict",
    "缺失材料": "missing_evidence",
    "人工复核": "manual_review",
    "是否触发人工复核": "manual_review",
    "硬性风险门槛": "hard_gate",
    "是否触发硬性风险门槛": "hard_gate",
}

CLAIM_HEADER_ALIASES = {
    "claim_id": "claim_id",
    "category": "category",
    "field_name": "field_name",
    "value": "value",
    "unit": "unit",
    "period": "period",
    "subject_name": "subject_name",
    "subject_identifier": "subject_identifier",
    "status": "status",
    "source_ids": "source_ids",
    "source_location": "source_location",
    "confidence": "confidence",
    "conflict_note": "conflict_note",
    "as_of_eligible": "as_of_eligible",
}

FAILURE_MARKERS = (
    "失败",
    "403",
    "internal error",
    "无法访问",
    "未取得",
    "未完成查询",
    "not accessible",
    "failed",
)
SUCCESS_MARKERS = (
    "成功",
    "success",
    "已核阅",
    "已访问",
    "verified_access",
    "accessible",
    "前序成功",
    "此前已成功",
)


@dataclass(frozen=True)
class MarkdownTable:
    headers: list[str]
    rows: list[dict[str, str]]
    start_line: int


def _strip_markdown(value: str) -> str:
    value = value.strip()
    while len(value) >= 4 and value.startswith("**") and value.endswith("**"):
        value = value[2:-2].strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        value = value[1:-1].strip()
    return value


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def extract_tables(text: str) -> list[MarkdownTable]:
    lines = text.splitlines()
    tables: list[MarkdownTable] = []
    index = 0
    while index + 1 < len(lines):
        headers = _split_table_row(lines[index])
        if not headers or not _is_separator_row(lines[index + 1]):
            index += 1
            continue
        rows: list[dict[str, str]] = []
        cursor = index + 2
        while cursor < len(lines):
            cells = _split_table_row(lines[cursor])
            if not cells:
                break
            if len(cells) < len(headers):
                cells += [""] * (len(headers) - len(cells))
            elif len(cells) > len(headers):
                # Unescaped pipes are invalid Markdown but occasionally appear in
                # generated prose. Preserve the overflow in the last column.
                cells = cells[: len(headers) - 1] + [" | ".join(cells[len(headers) - 1 :])]
            rows.append(dict(zip(headers, cells)))
            cursor += 1
        tables.append(MarkdownTable(headers=headers, rows=rows, start_line=index + 1))
        index = max(cursor, index + 2)
    return tables


def extract_embedded_json(text: str) -> dict[str, Any]:
    matches = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if not matches:
        raise ValueError("no fenced JSON object found")
    # A case may contain small examples; the final machine-readable block is the
    # largest valid object.
    errors: list[str] = []
    for candidate in sorted(matches, key=len, reverse=True):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
    raise ValueError("all fenced JSON objects are invalid: " + "; ".join(errors[:3]))


def _canonical_row(row: dict[str, str], aliases: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    extras: dict[str, str] = {}
    for raw_key, raw_value in row.items():
        key = aliases.get(raw_key, aliases.get(raw_key.lower()))
        if key:
            normalized[key] = raw_value.strip()
        elif raw_value.strip():
            extras[raw_key] = raw_value.strip()
    if extras:
        normalized["extra"] = json.dumps(extras, ensure_ascii=False, sort_keys=True)
    return normalized


def _normalize_status(value: Any) -> str:
    raw = _strip_markdown(str(value or "")).lower().replace(" ", "_")
    aliases = {
        "已核实": "verified",
        "核实": "verified",
        "未核实": "unverified",
        "冲突": "conflicting",
        "不适用": "not_applicable",
        "未发现记录": "no_record_found",
        "no-record-found": "no_record_found",
    }
    return aliases.get(raw, raw or "unverified")


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    raw = _strip_markdown(str(value or "")).lower()
    if raw in {"true", "yes", "是", "1"}:
        return True
    if raw in {"false", "no", "否", "0"}:
        return False
    return None


def _parse_scalar(value: str) -> Any:
    raw = _strip_markdown(value)
    if raw.lower() in {"null", "none"}:
        return None
    compact = raw.replace(",", "")
    if re.fullmatch(r"-?\d+", compact):
        try:
            return int(compact)
        except ValueError:
            return raw
    if re.fullmatch(r"-?\d+\.\d+", compact):
        try:
            return float(compact)
        except ValueError:
            return raw
    return raw


def _known_ids(value: Any, ids: Iterable[str]) -> list[str]:
    text = str(value or "")
    found: list[tuple[int, str]] = []
    for identifier in sorted(set(ids), key=len, reverse=True):
        match = re.search(rf"(?<![A-Za-z0-9]){re.escape(identifier)}(?![A-Za-z0-9])", text)
        if match:
            found.append((match.start(), identifier))
    return [identifier for _, identifier in sorted(found)]


def _access_status_class(value: Any) -> str:
    raw = _strip_markdown(str(value or "")).lower()
    has_success = any(marker in raw for marker in SUCCESS_MARKERS)
    has_failure = any(marker in raw for marker in FAILURE_MARKERS) or "超时" in raw
    if has_success and has_failure:
        return "partial"
    if has_success:
        return "success"
    if has_failure:
        return "failed"
    return "unknown"


def _date_prefix(value: Any) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _as_of_eligible(source: dict[str, Any], cutoff: str | None) -> bool | None:
    explicit = source.get("as_of_eligible")
    parsed = _parse_bool(explicit)
    if parsed is not None:
        return parsed
    published = _date_prefix(source.get("publication_date"))
    if published and cutoff:
        return published <= cutoff
    return None


def _extract_section(text: str, heading_pattern: str, end_pattern: str) -> str:
    start = re.search(heading_pattern, text, flags=re.MULTILINE)
    if not start:
        return ""
    tail = text[start.start() :]
    end = re.search(end_pattern, tail[start.end() - start.start() :], flags=re.MULTILINE)
    if not end:
        return tail.strip() + "\n"
    absolute_end = start.end() + end.start()
    return text[start.start() : absolute_end].strip() + "\n"


def _jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    data = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(data, encoding="utf-8")


def _json_file(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _case_id(path: Path, embedded: dict[str, Any]) -> str:
    value = (embedded.get("case") or {}).get("case_id") or embedded.get("case_id")
    if value:
        return str(value)
    match = re.search(r"case_\d+", path.stem, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot infer case id from {path.name}")
    return match.group(0).lower()


def _research_cutoff(case: dict[str, Any]) -> str | None:
    return case.get("research_cutoff") or case.get("research_cutoff_date") or case.get("cutoff_date")


def _scenario(case: dict[str, Any]) -> Any:
    return case.get("business_scenario") or case.get("business_scenarios") or case.get("scenario")


def _business_types(scenario: Any) -> tuple[list[str], str | None]:
    text = json.dumps(scenario, ensure_ascii=False) if not isinstance(scenario, str) else scenario
    types: list[str] = []
    if "历史授信" in text or "重做历史" in text:
        types.append("historical_credit_review")
    if "保理" in text:
        types.append("factoring")
    if "供应链" in text or "供应商融资" in text or "供应商授信" in text:
        types.append("supply_chain_finance")
    if "小微" in text or "经营贷" in text:
        types.append("micro_loan")
    primary = types[0] if types else None
    return types, primary


def _confidence_ordinal(value: Any) -> Any:
    """Return an explicitly non-calibrated ordinal score for cross-case sorting."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {key: _confidence_ordinal(item) for key, item in value.items()}
    raw = str(value or "").strip().lower().replace("_", "")
    mapping = {
        "高": 0.9,
        "high": 0.9,
        "中高": 0.8,
        "mediumhigh": 0.8,
        "中": 0.6,
        "medium": 0.6,
        "中低": 0.4,
        "mediumlow": 0.4,
        "低": 0.2,
        "low": 0.2,
    }
    return mapping.get(raw)


def _normalize_assessment(reference: dict[str, Any]) -> dict[str, Any]:
    raw_gates = list(reference.get("hard_gate_triggers") or [])
    information_keywords = (
        "未核验",
        "未确认",
        "缺失",
        "资料",
        "真实性",
        "确权",
        "债务人",
        "转让",
        "重复融资",
        "identity",
        "document",
        "evidence",
        "unverified",
    )
    information_gates: list[Any] = []
    adverse_gates: list[Any] = []
    for gate in raw_gates:
        gate_text = json.dumps(gate, ensure_ascii=False).lower()
        gate_type = gate.get("type") if isinstance(gate, dict) else None
        if gate_type == "information_gate" or any(key in gate_text for key in information_keywords):
            information_gates.append(gate)
        else:
            adverse_gates.append(gate)
    missing = list(reference.get("missing_critical_evidence") or [])
    readiness = "insufficient_evidence" if information_gates or missing else "not_assessed"
    action = str(reference.get("recommended_action") or "")
    if "人工" in action:
        decision_status = "manual_review"
    elif any(word in action for word in ("拒绝", "暂缓", "不予", "暂停")):
        decision_status = "blocked"
    elif any(word in action for word in ("通过", "准入", "批准")):
        decision_status = "conditionally_eligible"
    else:
        decision_status = "not_assessed"
    confidence_raw = reference.get("confidence")
    return {
        "risk_level_raw": reference.get("risk_level"),
        "risk_scope": reference.get("risk_scope"),
        "corporate_risk_level": reference.get("corporate_risk_level"),
        "transaction_risk_level": reference.get("specific_factoring_transaction_risk")
        or reference.get("transaction_risk_level"),
        "transaction_readiness": readiness,
        "decision_status": decision_status,
        "adverse_risk_gates": adverse_gates,
        "information_gates": information_gates,
        "assessment_confidence": {
            "raw": confidence_raw,
            "ordinal_score": _confidence_ordinal(confidence_raw),
            "calibrated_probability": False,
        },
    }


def normalize_case(path: Path, output_root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    input_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    embedded = extract_embedded_json(text)
    case = dict(embedded.get("case") or {})
    case_id = _case_id(path, embedded)
    cutoff = _research_cutoff(case)
    tables = extract_tables(text)

    source_tables = [
        t
        for t in tables
        if "source_id" in t.headers
        and any(header in SOURCE_HEADER_ALIASES for header in t.headers if header != "source_id")
        and any(
            SOURCE_HEADER_ALIASES.get(header, SOURCE_HEADER_ALIASES.get(header.lower())) == "direct_url"
            for header in t.headers
        )
    ]
    claim_tables = [t for t in tables if "claim_id" in t.headers]
    checklist_tables = [t for t in tables if "检查项目" in t.headers]

    embedded_sources = {
        str(row.get("source_id")): dict(row)
        for row in embedded.get("sources") or []
        if row.get("source_id")
    }
    table_sources: list[dict[str, Any]] = []
    for table in source_tables:
        for row in table.rows:
            item = _canonical_row(row, SOURCE_HEADER_ALIASES)
            if item.get("source_id"):
                table_sources.append(item)

    source_order: list[str] = []
    for row in table_sources + list(embedded_sources.values()):
        source_id = str(row.get("source_id") or "")
        if source_id and source_id not in source_order:
            source_order.append(source_id)

    normalized_sources: list[dict[str, Any]] = []
    for source_id in source_order:
        merged = dict(embedded_sources.get(source_id) or {})
        markdown_row = next((row for row in table_sources if row.get("source_id") == source_id), {})
        merged.update({k: v for k, v in markdown_row.items() if v != ""})
        merged["source_id"] = source_id
        direct_url = merged.get("direct_url") or merged.get("url")
        if direct_url:
            merged["direct_url"] = _strip_markdown(str(direct_url))
            merged["url"] = merged["direct_url"]
        merged["access_status_class"] = _access_status_class(merged.get("access_status"))
        merged["eligible_at_cutoff"] = _as_of_eligible(merged, cutoff)
        merged["is_primary_evidence"] = merged["access_status_class"] in {"success", "partial"}
        normalized_sources.append(merged)

    source_ids = [row["source_id"] for row in normalized_sources]
    embedded_claim_rows = list(embedded.get("claims") or [])
    embedded_claims: dict[str, dict[str, Any]] = {}
    for row in embedded_claim_rows:
        if isinstance(row, dict) and row.get("claim_id"):
            embedded_claims[str(row["claim_id"])] = dict(row)
        elif isinstance(row, str):
            # Some packs use the JSON block only as a compact ID inventory; the
            # complete typed rows still live in the Markdown tables.
            embedded_claims[row] = {"claim_id": row}
    table_claims: list[dict[str, Any]] = []
    for table in claim_tables:
        for row in table.rows:
            item = _canonical_row(row, CLAIM_HEADER_ALIASES)
            if item.get("claim_id"):
                table_claims.append(item)

    claim_order: list[str] = []
    for row in table_claims + list(embedded_claims.values()):
        claim_id = str(row.get("claim_id") or "")
        if claim_id and claim_id not in claim_order:
            claim_order.append(claim_id)

    normalized_claims: list[dict[str, Any]] = []
    for claim_id in claim_order:
        merged = dict(embedded_claims.get(claim_id) or {})
        markdown_row = next((row for row in table_claims if row.get("claim_id") == claim_id), {})
        if markdown_row:
            raw_value = markdown_row.get("value", "")
            if "value" not in merged:
                merged["value"] = _parse_scalar(raw_value)
            merged["value_raw"] = raw_value
            for key, value in markdown_row.items():
                if key not in {"value", "source_ids", "as_of_eligible", "status"} and value != "":
                    merged[key] = value
        merged["claim_id"] = claim_id
        merged["status"] = _normalize_status(markdown_row.get("status", merged.get("status")))
        merged["source_ids"] = _known_ids(
            markdown_row.get("source_ids", merged.get("source_ids")), source_ids
        ) or list(merged.get("source_ids") or [])
        explicit_as_of = markdown_row.get("as_of_eligible", merged.get("as_of_eligible"))
        merged["as_of_eligible"] = _parse_bool(explicit_as_of)
        normalized_claims.append(merged)

    claim_ids = [row["claim_id"] for row in normalized_claims]
    source_by_id = {row["source_id"]: row for row in normalized_sources}
    normalized_checklist: list[dict[str, Any]] = []
    for table in checklist_tables:
        for index, row in enumerate(table.rows, start=1):
            item = _canonical_row(row, CHECKLIST_HEADER_ALIASES)
            if not item.get("item"):
                continue
            item["check_id"] = f"CHK{len(normalized_checklist) + 1:03d}"
            item["status"] = _normalize_status(item.get("status"))
            item["evidence_ids"] = _known_ids(
                item.get("evidence_ids"), [*source_ids, *claim_ids]
            )
            item["evidence_claim_ids"] = [
                evidence_id for evidence_id in item["evidence_ids"] if evidence_id in claim_ids
            ]
            item["manual_review"] = _parse_bool(item.get("manual_review"))
            item["hard_gate"] = _parse_bool(item.get("hard_gate"))
            normalized_checklist.append(item)

    # Split the source packs' mixed evidence column into claims, usable direct
    # sources, and failed retrieval attempts.
    for item in normalized_checklist:
        item["direct_source_ids"] = [
            evidence_id
            for evidence_id in item["evidence_ids"]
            if evidence_id in source_ids
            and source_by_id[evidence_id].get("access_status_class") != "failed"
        ]
        item["retrieval_attempt_ids"] = [
            evidence_id
            for evidence_id in item["evidence_ids"]
            if evidence_id in source_ids
            and source_by_id[evidence_id].get("access_status_class") == "failed"
        ]
    post_cutoff_claims: list[dict[str, Any]] = []
    reference_claims: list[dict[str, Any]] = []
    for claim in normalized_claims:
        category = str(claim.get("category") or "").lower()
        supporting_sources = [
            source_by_id[source_id]
            for source_id in claim.get("source_ids") or []
            if source_id in source_by_id
        ]
        verified_only_after_cutoff = (
            claim.get("status") in {"verified", "conflicting"}
            and bool(supporting_sources)
            and all(source.get("eligible_at_cutoff") is False for source in supporting_sources)
        )
        # as_of_eligible=false also appears on transaction facts that simply were
        # unavailable at the cutoff. Those are evidence gaps, not future outcomes.
        is_post_cutoff = "post_cutoff" in category or verified_only_after_cutoff
        (post_cutoff_claims if is_post_cutoff else reference_claims).append(claim)
    retrieval_failures = [
        source for source in normalized_sources if source.get("access_status_class") == "failed"
    ]

    reference_assessment = dict(embedded.get("reference_assessment") or {})
    if reference_assessment.get("confidence") is not None and not isinstance(
        reference_assessment["confidence"], dict
    ):
        reference_assessment["confidence"] = {
            "overall_reference_assessment": reference_assessment["confidence"]
        }
    reference_assessment["label_grade"] = "silver"
    reference_assessment["generated_reference"] = True
    reference_assessment["human_review_status"] = "not_reviewed"
    reference_assessment["eval_only"] = True
    reference_assessment["normalized"] = _normalize_assessment(reference_assessment)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def issue(target: list[dict[str, Any]], code: str, detail: str) -> None:
        target.append({"code": code, "detail": detail})

    if len(source_ids) != len(set(source_ids)):
        issue(errors, "duplicate_source_id", "normalized sources contain duplicate ids")
    if len(claim_ids) != len(set(claim_ids)):
        issue(errors, "duplicate_claim_id", "normalized claims contain duplicate ids")
    for claim in normalized_claims:
        for source_id in claim.get("source_ids") or []:
            if source_id not in source_by_id:
                issue(errors, "dangling_claim_source", f"{claim['claim_id']} -> {source_id}")
        supporting = [source_by_id[s] for s in claim.get("source_ids") or [] if s in source_by_id]
        if claim.get("status") in {"verified", "conflicting"} and supporting:
            if all(s.get("access_status_class") == "failed" for s in supporting):
                issue(
                    errors,
                    "verified_from_failed_sources",
                    f"{claim['claim_id']} is {claim['status']} but every source failed",
                )
    for claim_id in reference_assessment.get("reasoning_claim_ids") or []:
        if claim_id not in claim_ids:
            issue(errors, "dangling_assessment_claim", str(claim_id))
    for item in normalized_checklist:
        for evidence_id in item.get("evidence_ids") or []:
            if evidence_id not in source_ids and evidence_id not in claim_ids:
                issue(errors, "dangling_checklist_evidence", f"{item['check_id']} -> {evidence_id}")

    if len(embedded_claims) != len(normalized_claims):
        issue(
            warnings,
            "embedded_json_claims_incomplete",
            f"embedded={len(embedded_claims)}, normalized_markdown_merge={len(normalized_claims)}",
        )
    if len(embedded_sources) != len(normalized_sources):
        issue(
            warnings,
            "embedded_json_sources_incomplete",
            f"embedded={len(embedded_sources)}, normalized_markdown_merge={len(normalized_sources)}",
        )
    if not normalized_checklist:
        issue(warnings, "missing_checklist_table", "no Markdown checklist table found")
    if any(source.get("eligible_at_cutoff") is None for source in normalized_sources):
        issue(warnings, "source_cutoff_unknown", "one or more sources have unknown cutoff eligibility")
    if any("sina" in str(source.get("direct_url", "")).lower() for source in normalized_sources):
        issue(warnings, "mirror_source_present", "one or more sources use a Sina mirror")
    issue(
        warnings,
        "raw_sources_not_downloaded",
        "this pass creates an acquisition queue; source files still require download and hashing",
    )

    case_dir = output_root / case_id
    reference_dir = case_dir / "reference"
    post_cutoff_dir = case_dir / "post_cutoff"
    case_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)
    post_cutoff_dir.mkdir(parents=True, exist_ok=True)

    scenario = _scenario(case)
    business_types, primary_business_type = _business_types(scenario)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "original_document": path.as_posix(),
        "original_sha256": input_sha256,
        "research_cutoff": cutoff,
        "difficulty": case.get("difficulty"),
        "business_scenario": scenario,
        "business_types": business_types,
        "primary_business_type": primary_business_type,
        "subject": embedded.get("subject") or {},
        "label_policy": {
            "grade": "silver",
            "generated_by": "deep_research",
            "human_review_status": "not_reviewed",
            "reference_retrievable_by_agent": False,
            "raw_sources_retrievable_by_agent_after_verification": True,
        },
        "counts": {
            "sources": len(normalized_sources),
            "reference_claims": len(reference_claims),
            "post_cutoff_claims": len(post_cutoff_claims),
            "checklist_items": len(normalized_checklist),
        },
    }

    download_queue: list[dict[str, Any]] = []
    rag_manifest: list[dict[str, Any]] = []
    for source in normalized_sources:
        source_id = source["source_id"]
        file_type = str(source.get("file_type") or "bin").lower()
        extension = "pdf" if "pdf" in file_type else "html" if "html" in file_type else "bin"
        target = f"raw_sources/{source_id}.{extension}"
        entry = {
            "case_id": case_id,
            "source_id": source_id,
            "url": source.get("direct_url"),
            "target_path": target,
            "eligible_at_cutoff": source.get("eligible_at_cutoff"),
            "access_status_class": source.get("access_status_class"),
        }
        if source.get("direct_url") and source.get("access_status_class") != "failed":
            download_queue.append(entry)
        if source.get("eligible_at_cutoff") is True and source.get("access_status_class") in {
            "success",
            "partial",
        }:
            rag_manifest.append(
                {
                    **entry,
                    "ready_for_ingest": False,
                    "document_role": "primary_source",
                    "retrieval_collection": f"eval_{case_id}_sources",
                }
            )

    report = _extract_section(
        text,
        r"^##\s+.*(?:离线评测参考报告|参考报告)\s*$",
        r"^##\s+.*(?:参考风险判断|离线参考风险判断)\s*$",
    )
    if not report:
        issue(warnings, "reference_report_not_extracted", "reference report heading not found")

    _json_file(case_dir / "manifest.json", manifest)
    _jsonl(case_dir / "sources.jsonl", normalized_sources)
    _jsonl(case_dir / "download_queue.jsonl", download_queue)
    _jsonl(case_dir / "rag_manifest.jsonl", rag_manifest)
    _jsonl(case_dir / "retrieval_failures.jsonl", retrieval_failures)
    _jsonl(reference_dir / "claims.jsonl", reference_claims)
    _json_file(reference_dir / "checklist.json", normalized_checklist)
    _json_file(reference_dir / "assessment.json", reference_assessment)
    _json_file(reference_dir / "limitations.json", embedded.get("limitations") or [])
    (reference_dir / "report.md").write_text(report, encoding="utf-8")
    _jsonl(post_cutoff_dir / "claims.jsonl", post_cutoff_claims)
    _json_file(post_cutoff_dir / "outcomes.json", embedded.get("post_cutoff_outcomes") or [])

    validation = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "status": "failed" if errors else "passed_with_warnings" if warnings else "passed",
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "markdown_source_rows": len(table_sources),
            "embedded_source_rows": len(embedded_sources),
            "normalized_source_rows": len(normalized_sources),
            "markdown_claim_rows": len(table_claims),
            "embedded_claim_rows": len(embedded_claim_rows),
            "normalized_claim_rows": len(normalized_claims),
            "reference_claim_rows": len(reference_claims),
            "post_cutoff_claim_rows": len(post_cutoff_claims),
            "checklist_rows": len(normalized_checklist),
        },
    }
    _json_file(case_dir / "validation_report.json", validation)
    return {"case_id": case_id, "path": case_dir.as_posix(), **validation}


def process_all(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    case_paths = sorted(input_dir.glob(CASE_GLOB))
    if not case_paths:
        raise SystemExit(f"no {CASE_GLOB} files found under {input_dir}")
    results: list[dict[str, Any]] = []
    for path in case_paths:
        try:
            results.append(normalize_case(path, output_dir))
        except Exception as exc:  # keep the other independent cases processable
            results.append(
                {
                    "case_id": path.stem,
                    "status": "failed",
                    "errors": [{"code": "processor_exception", "detail": str(exc)}],
                    "warnings": [],
                }
            )
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_directory": input_dir.as_posix(),
        "case_count": len(results),
        "failed_count": sum(result.get("status") == "failed" for result in results),
        "cases": [
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "error_count": len(result.get("errors") or []),
                "warning_count": len(result.get("warnings") or []),
                "metrics": result.get("metrics") or {},
            }
            for result in results
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_file(output_dir / "index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "real_cases_processed",
    )
    args = parser.parse_args()
    index = process_all(args.input_dir.resolve(), args.output_dir.resolve())
    print(json.dumps(index, ensure_ascii=False, indent=2))
    return 1 if index["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
