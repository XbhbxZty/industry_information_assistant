"""Persistence and validation boundary for administrator company profiles.

The service deliberately owns reconstruction of ``coverage``.  Accepting a
client-declared "queried" list would let a caller turn a missing event into an
apparently verified no-record conclusion without a trusted source.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.company_profile import AdminCompanyProfile, AdminCompanyProfileAudit
from schemas.company_profile import CompanyProfileWrite

try:
    from config.dd_checklist import (
        CHECKLIST, CORE_IDS, SCENARIO_CHECKLISTS, SCENARIO_IDS_BY_SCENARIO,
        build_field_checks,
    )
    from service.company_profile import fill_field_checks, profile_to_facts
    from service.profile_schema import validate_profile
    from service.verification import parse_iso
except ImportError:  # pragma: no cover - package import compatibility
    from app.config.dd_checklist import (
        CHECKLIST, CORE_IDS, SCENARIO_CHECKLISTS, SCENARIO_IDS_BY_SCENARIO,
        build_field_checks,
    )
    from app.service.company_profile import fill_field_checks, profile_to_facts
    from app.service.profile_schema import validate_profile
    from app.service.verification import parse_iso


MAX_MATERIALS = 20
MAX_MATERIAL_CHARS = 12_000
MAX_MATERIAL_TOTAL_CHARS = 100_000
TRUSTED_FIELD_SOURCE_TYPES = frozenset({"official", "authorized", "audited"})
SCENARIO_NUMBER_INPUT_IDS = frozenset({
    "accounts_receivable_gross", "accounts_receivable_net",
    "accounts_receivable_impairment", "top5_customer_share",
    "top5_supplier_share", "inventory", "capex_cash_outflow",
    "construction_in_progress", "overseas_revenue", "guarantee_balance",
})
FIELD_SOURCE_KEYS = frozenset({
    "source_id", "name", "issuer", "source_type", "field_ids", "retrieved_at",
    "as_of_date", "reference", "sha256",
})
MATERIAL_KEYS = frozenset({
    "material_id", "source_type", "title", "content", "reference", "as_of_date",
    "date_unknown_reason", "eligible_for_structured_evidence",
})


class AdminCompanyProfileError(ValueError):
    """Base class for router-mappable profile failures."""


class AdminCompanyProfileNotFound(AdminCompanyProfileError):
    pass


class AdminCompanyProfileConflict(AdminCompanyProfileError):
    pass


class AdminCompanyProfileValidationError(AdminCompanyProfileError):
    pass


class AdminCompanyProfileIntegrityError(AdminCompanyProfileValidationError):
    """Persisted profile/audit state is inconsistent and must fail closed."""


def _json_copy(value: Any) -> Any:
    """Reject non-JSON inputs instead of stringifying them into audit history."""
    try:
        return json.loads(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise AdminCompanyProfileValidationError("档案内容必须完全可 JSON 序列化") from exc


def _content_sha256(content: Dict[str, Any]) -> str:
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso_key(value: str, label: str) -> datetime:
    parsed = parse_iso(value)
    if parsed is None:
        raise AdminCompanyProfileValidationError(f"{label} 必须是合法 ISO 日期或时间")
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _normalise_credit_code(value: Any) -> Optional[str]:
    code = str(value or "").strip().upper()
    return code or None


def _selected_field_ids(scenario: str) -> set[str]:
    return set(CORE_IDS) | set(SCENARIO_IDS_BY_SCENARIO.get(scenario or "", frozenset()))


def _normalise_sources(sources: Iterable[Dict[str, Any]], scenario: str) -> List[Dict[str, Any]]:
    allowed_ids = _selected_field_ids(scenario)
    out: List[Dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for row in sources:
        source = _json_copy(row)
        extra = sorted(set(source) - FIELD_SOURCE_KEYS)
        if extra:
            raise AdminCompanyProfileValidationError(
                f"field_sources 含有不支持的字段：{extra}")
        source_id = str(source.get("source_id") or "").strip()
        if not source_id or source_id in seen_source_ids:
            raise AdminCompanyProfileValidationError("field_sources.source_id 必须非空且在档案内唯一")
        seen_source_ids.add(source_id)
        for key in ("name", "issuer", "reference"):
            if not str(source.get(key) or "").strip():
                raise AdminCompanyProfileValidationError(f"field_sources.{key} 必须非空")
        if source.get("source_type") not in TRUSTED_FIELD_SOURCE_TYPES:
            raise AdminCompanyProfileValidationError("field_sources 只接受 official/authorized/audited")
        field_ids = [str(fid).strip() for fid in source.get("field_ids") or []]
        if not field_ids or len(set(field_ids)) != len(field_ids):
            raise AdminCompanyProfileValidationError("field_sources.field_ids 必须非空且不重复")
        unknown = sorted(set(field_ids) - allowed_ids)
        if unknown:
            raise AdminCompanyProfileValidationError(
                f"field_sources 声明了当前场景不可用的字段：{unknown}")
        retrieved_at_raw = str(source.get("retrieved_at") or "").strip()
        as_of_date_raw = str(source.get("as_of_date") or "").strip()
        retrieved_at = _iso_key(
            retrieved_at_raw,
            "field_sources.retrieved_at",
        )
        as_of_date = _iso_key(
            as_of_date_raw,
            "field_sources.as_of_date",
        )
        # ``as_of_date`` 通常只有日粒度。此时应按来源字符串中的本地日历日
        # 比较，不能先把带偏移的 retrieved_at 转成 UTC；否则同一当地日期
        # 的凌晨取证会被误判成“先取证、后发生”。若 as_of 声明到具体时刻，
        # 才按规范化后的绝对时间比较。
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of_date_raw):
            retrieved_parsed = parse_iso(retrieved_at_raw)
            impossible_order = (
                retrieved_parsed is not None
                and as_of_date.date() > retrieved_parsed.date()
            )
        else:
            impossible_order = as_of_date > retrieved_at
        if impossible_order:
            raise AdminCompanyProfileValidationError(
                "field_sources.as_of_date 不得晚于 retrieved_at"
            )
        digest = source.get("sha256")
        if digest and not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest)):
            raise AdminCompanyProfileValidationError("field_sources.sha256 必须是 64 位十六进制摘要")
        source["source_id"] = source_id
        source["name"] = str(source["name"]).strip()
        source["issuer"] = str(source["issuer"]).strip()
        source["reference"] = str(source["reference"]).strip()
        source["field_ids"] = sorted(field_ids)
        source["sha256"] = str(digest).lower() if digest else None
        out.append(source)
    return out


def _normalise_materials(materials: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [_json_copy(m) for m in materials]
    if len(rows) > MAX_MATERIALS:
        raise AdminCompanyProfileValidationError(f"materials 最多 {MAX_MATERIALS} 条")
    total = 0
    seen_ids: set[str] = set()
    out: List[Dict[str, Any]] = []
    for index, material in enumerate(rows, start=1):
        extra = sorted(set(material) - MATERIAL_KEYS)
        if extra:
            raise AdminCompanyProfileValidationError(
                f"materials[{index - 1}] 含有不支持的字段：{extra}")
        content = str(material.get("content") or "")
        if not content or len(content) > MAX_MATERIAL_CHARS:
            raise AdminCompanyProfileValidationError(
                f"materials[{index - 1}].content 必须为 1-{MAX_MATERIAL_CHARS} 字符")
        total += len(content)
        if total > MAX_MATERIAL_TOTAL_CHARS:
            raise AdminCompanyProfileValidationError(
                f"materials 内容总量不能超过 {MAX_MATERIAL_TOTAL_CHARS} 字符")
        if material.get("source_type") not in {"company_submitted", "admin_observation"}:
            raise AdminCompanyProfileValidationError("materials.source_type 必须为 company_submitted 或 admin_observation")
        title = str(material.get("title") or "").strip()
        if not title:
            raise AdminCompanyProfileValidationError(f"materials[{index - 1}].title 必须非空")
        as_of = material.get("as_of_date")
        unknown_reason = material.get("date_unknown_reason")
        if bool(as_of) == bool(unknown_reason):
            raise AdminCompanyProfileValidationError(
                "每条 material 必须二选一提供 as_of_date 或 date_unknown_reason")
        if as_of:
            _iso_key(str(as_of), f"materials[{index - 1}].as_of_date")
        material_id = str(material.get("material_id") or f"material_{index}").strip()
        if not material_id or material_id in seen_ids:
            raise AdminCompanyProfileValidationError("materials.material_id 必须在档案内唯一")
        seen_ids.add(material_id)
        material["material_id"] = material_id
        material["title"] = title
        material["eligible_for_structured_evidence"] = False
        out.append(material)
    return out


def _profile_snapshot_values(
    profile: Dict[str, Any], scenario: str, scenario_data: Dict[str, Any],
    field_sources: List[Dict[str, Any]], materials: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "profile": _json_copy(profile),
        "scenario": scenario,
        "scenario_data": _json_copy(scenario_data),
        "field_sources": _json_copy(field_sources),
        "materials": _json_copy(materials),
    }


def prepare_company_profile_content(write: CompanyProfileWrite) -> Dict[str, Any]:
    """Validate a full profile and deterministically rebuild its coverage."""
    profile = _json_copy(write.profile)
    if not isinstance(profile, dict):
        raise AdminCompanyProfileValidationError("profile 必须是对象")
    name = str(profile.get("name") or "").strip()
    if not name:
        raise AdminCompanyProfileValidationError("profile.name 是创建档案的唯一必填业务字段")
    if "required" in profile:
        raise AdminCompanyProfileValidationError("不得保存自定义 required；尽调必查项只由系统清单定义")
    profile["name"] = name
    credit_code = _normalise_credit_code(profile.get("credit_code"))
    if credit_code:
        profile["credit_code"] = credit_code
    else:
        profile.pop("credit_code", None)

    scenario = str(write.scenario or "")
    if scenario not in {"", "factoring"}:
        raise AdminCompanyProfileValidationError("scenario 只支持空值或 factoring")
    scenario_data = _json_copy(write.scenario_data)
    if not isinstance(scenario_data, dict):
        raise AdminCompanyProfileValidationError("scenario_data 必须是对象")
    allowed_scenario = set(SCENARIO_IDS_BY_SCENARIO.get(scenario, frozenset()))
    unknown_scenario_keys = sorted(set(scenario_data) - allowed_scenario)
    if unknown_scenario_keys:
        raise AdminCompanyProfileValidationError(
            f"scenario_data 只能包含所选场景的字段：{unknown_scenario_keys}")

    field_sources = _normalise_sources(
        [source.model_dump() if hasattr(source, "model_dump") else source
         for source in write.field_sources], scenario)
    materials = _normalise_materials(
        [material.model_dump() if hasattr(material, "model_dump") else material
         for material in write.materials])

    covered_ids = {fid for source in field_sources for fid in source["field_ids"]}
    if scenario_data and not set(scenario_data).issubset(covered_ids):
        missing = sorted(set(scenario_data) - covered_ids)
        raise AdminCompanyProfileValidationError(
            f"scenario_data 字段必须由受信 field_source 覆盖：{missing}")

    # Client coverage is intentionally discarded.  A source can only claim the
    # fields it explicitly covers; the earliest retrieval time is conservative.
    profile.pop("coverage", None)
    if field_sources:
        earliest = min(field_sources, key=lambda source: _iso_key(source["retrieved_at"], "retrieved_at"))
        profile["coverage"] = {
            "queried": sorted(covered_ids),
            "retrieved_at": earliest["retrieved_at"],
        }
    else:
        profile["coverage"] = {"queried": []}

    problems = validate_profile(profile)
    if problems:
        raise AdminCompanyProfileValidationError("；".join(problems))

    # Reuse production projection rules to prove that every field that would
    # become verified has a trusted field-level source.  This preserves the
    # attribute-empty anomaly and event-empty no-record semantics in one place.
    try:
        checks = build_field_checks(scenario=scenario)
        fill_field_checks(profile, profile_to_facts(profile), checks)
    except (KeyError, TypeError, ValueError) as exc:
        raise AdminCompanyProfileValidationError(f"档案无法按生产尽调映射消费：{exc}") from exc
    uncovered_verified = sorted(
        check["field_id"] for check in checks
        if check.get("status") == "verified" and check.get("field_id") not in covered_ids
    )
    if uncovered_verified:
        raise AdminCompanyProfileValidationError(
            f"会成为 verified 的结构化字段缺少受信 field_source：{uncovered_verified}")

    values = _profile_snapshot_values(profile, scenario, scenario_data, field_sources, materials)
    # ``name``/``credit_code`` below are relational query columns duplicated
    # from ``profile``.  They are intentionally excluded from the digest so
    # the service can recompute the exact same hash from persisted JSON alone.
    content_sha256 = _content_sha256(values)
    values["name"] = name
    values["credit_code"] = credit_code
    values["content_sha256"] = content_sha256
    return values


def _row_snapshot(row: AdminCompanyProfile) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "credit_code": row.credit_code,
        "status": row.status,
        "revision": int(row.revision),
        "content_sha256": row.content_sha256,
        "profile": _json_copy(row.profile_json),
        "scenario": row.scenario or "",
        "scenario_data": _json_copy(row.scenario_data or {}),
        "field_sources": _json_copy(row.field_sources or []),
        "materials": _json_copy(row.materials or []),
    }


def _required_actor_id(actor_id: Any) -> str:
    if not isinstance(actor_id, str):
        raise AdminCompanyProfileValidationError("审计 actor_id 必须为字符串")
    actor = actor_id.strip()
    if not actor or len(actor) > 64:
        raise AdminCompanyProfileValidationError("审计 actor_id 必须为 1-64 字符")
    return actor


def _required_change_reason(reason: Any) -> str:
    if not isinstance(reason, str):
        raise AdminCompanyProfileValidationError("审计 change_reason 必须为字符串")
    value = reason.strip()
    if not value or len(value) > 500:
        raise AdminCompanyProfileValidationError("审计 change_reason 必须为 1-500 字符")
    return value


def _snapshot_content_sha256(snapshot: Dict[str, Any]) -> str:
    required = {
        "id", "name", "credit_code", "status", "revision", "content_sha256",
        "profile", "scenario", "scenario_data", "field_sources", "materials",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != required:
        raise AdminCompanyProfileValidationError("企业档案审计快照字段不完整")
    if not isinstance(snapshot["id"], str) or not snapshot["id"]:
        raise AdminCompanyProfileValidationError("企业档案审计 id 非法")
    if not isinstance(snapshot["name"], str) or not snapshot["name"].strip():
        raise AdminCompanyProfileValidationError("企业档案审计 name 非法")
    if snapshot["credit_code"] is not None and not isinstance(snapshot["credit_code"], str):
        raise AdminCompanyProfileValidationError("企业档案审计 credit_code 非法")
    if snapshot["status"] not in {"active", "archived"}:
        raise AdminCompanyProfileValidationError("企业档案审计 status 非法")
    if type(snapshot["revision"]) is not int or snapshot["revision"] < 1:
        raise AdminCompanyProfileValidationError("企业档案审计 revision 必须为正整数")
    if not isinstance(snapshot["content_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", snapshot["content_sha256"],
    ):
        raise AdminCompanyProfileValidationError("企业档案审计 content_sha256 非法")
    profile = snapshot["profile"]
    if not isinstance(profile, dict):
        raise AdminCompanyProfileValidationError("企业档案审计 profile 必须是对象")
    if not isinstance(snapshot["scenario"], str):
        raise AdminCompanyProfileValidationError("企业档案审计 scenario 必须是字符串")
    if not isinstance(snapshot["scenario_data"], dict):
        raise AdminCompanyProfileValidationError("企业档案审计 scenario_data 必须是对象")
    if not isinstance(snapshot["field_sources"], list):
        raise AdminCompanyProfileValidationError("企业档案审计 field_sources 必须是数组")
    if not isinstance(snapshot["materials"], list):
        raise AdminCompanyProfileValidationError("企业档案审计 materials 必须是数组")

    class _StoredSnapshot:
        profile = snapshot["profile"]
        scenario = snapshot["scenario"]
        scenario_data = snapshot["scenario_data"]
        field_sources = snapshot["field_sources"]
        materials = snapshot["materials"]

    rebuilt = prepare_company_profile_content(_StoredSnapshot())
    persisted_content = {
        "profile": snapshot["profile"],
        "scenario": snapshot["scenario"],
        "scenario_data": snapshot["scenario_data"],
        "field_sources": snapshot["field_sources"],
        "materials": snapshot["materials"],
    }
    rebuilt_content = {
        key: rebuilt[key]
        for key in ("profile", "scenario", "scenario_data", "field_sources", "materials")
    }
    if _content_sha256(persisted_content) != _content_sha256(rebuilt_content):
        raise AdminCompanyProfileValidationError("企业档案审计快照不是规范化领域数据")
    if snapshot["name"] != rebuilt["name"] or snapshot["credit_code"] != rebuilt["credit_code"]:
        raise AdminCompanyProfileValidationError("企业档案审计关系列投影不一致")
    return rebuilt["content_sha256"]


def validate_company_profile_audit_history(
    db: Session, profile_id: str,
) -> List[AdminCompanyProfileAudit]:
    """Validate structural audit continuity; this is not a cryptographic seal.

    The persisted audit hash chain is introduced only with the database
    migration stage.  Until then this rejects missing/duplicate revisions,
    broken before/after linkage, empty attribution, snapshot hash drift and a
    latest snapshot that no longer matches the current profile row.
    """
    try:
        row = _get_row(db, profile_id)
        if type(row.revision) is not int or row.revision < 1:
            raise AdminCompanyProfileIntegrityError("企业档案当前 revision 非法")
        for field_name in ("created_by", "updated_by"):
            raw_actor = getattr(row, field_name)
            if _required_actor_id(raw_actor) != raw_actor:
                raise AdminCompanyProfileIntegrityError(f"企业档案 {field_name} 归属信息非法")
        if row.status == "archived":
            if _required_actor_id(row.archived_by) != row.archived_by:
                raise AdminCompanyProfileIntegrityError("企业档案 archived_by 归属信息非法")
            if row.archived_at is None:
                raise AdminCompanyProfileIntegrityError("企业档案归档时间缺失")
        elif row.status != "active":
            raise AdminCompanyProfileIntegrityError("企业档案当前状态非法")
        elif row.archived_by is not None or row.archived_at is not None:
            raise AdminCompanyProfileIntegrityError("生效企业档案不得携带归档信息")

        audits = db.query(AdminCompanyProfileAudit).filter(
            AdminCompanyProfileAudit.profile_id == str(profile_id)
        ).order_by(
            AdminCompanyProfileAudit.revision.asc(),
            AdminCompanyProfileAudit.created_at.asc(),
            AdminCompanyProfileAudit.id.asc(),
        ).all()
        if len(audits) != int(row.revision):
            raise AdminCompanyProfileIntegrityError("企业档案审计修订数量不连续")

        previous_after: Optional[Dict[str, Any]] = None
        for expected_revision, audit in enumerate(audits, start=1):
            if type(audit.revision) is not int or audit.revision != expected_revision:
                raise AdminCompanyProfileIntegrityError("企业档案审计 revision 不连续")
            if str(audit.profile_id) != str(profile_id):
                raise AdminCompanyProfileIntegrityError("企业档案审计 profile_id 不匹配")
            if _required_actor_id(audit.actor_id) != audit.actor_id:
                raise AdminCompanyProfileIntegrityError("企业档案审计 actor_id 非规范值")
            if _required_change_reason(audit.change_reason) != audit.change_reason:
                raise AdminCompanyProfileIntegrityError("企业档案审计 change_reason 非规范值")

            after = _json_copy(audit.after_snapshot)
            computed_hash = _snapshot_content_sha256(after)
            if after.get("id") != str(profile_id) or after.get("revision") != expected_revision:
                raise AdminCompanyProfileIntegrityError("企业档案审计 after_snapshot 身份不匹配")
            if computed_hash != after.get("content_sha256") or computed_hash != audit.content_sha256:
                raise AdminCompanyProfileIntegrityError("企业档案审计快照哈希不匹配")

            if expected_revision == 1:
                if audit.action != "created" or audit.before_snapshot is not None:
                    raise AdminCompanyProfileIntegrityError("企业档案首条审计必须是 created")
            else:
                if audit.action not in {"updated", "archived"}:
                    raise AdminCompanyProfileIntegrityError("企业档案审计 action 非法")
                if previous_after is None or previous_after.get("status") != "active":
                    raise AdminCompanyProfileIntegrityError("企业档案归档后不得继续修订")
                before = _json_copy(audit.before_snapshot)
                if before != previous_after:
                    raise AdminCompanyProfileIntegrityError("企业档案审计前后快照不连续")

            expected_status = "archived" if audit.action == "archived" else "active"
            if after.get("status") != expected_status:
                raise AdminCompanyProfileIntegrityError("企业档案审计 action 与状态不一致")
            if audit.action == "archived" and expected_revision != len(audits):
                raise AdminCompanyProfileIntegrityError("归档审计之后不得存在后续修订")
            previous_after = after

        if previous_after != _row_snapshot(row):
            raise AdminCompanyProfileIntegrityError("企业档案当前版本与最新审计快照不一致")
        if row.created_by != audits[0].actor_id:
            raise AdminCompanyProfileIntegrityError("企业档案创建人和首条审计不一致")
        if row.updated_by != audits[-1].actor_id:
            raise AdminCompanyProfileIntegrityError("企业档案更新人与最新审计不一致")
        if row.status == "archived" and row.archived_by != audits[-1].actor_id:
            raise AdminCompanyProfileIntegrityError("企业档案归档人与归档审计不一致")
        return audits
    except AdminCompanyProfileIntegrityError:
        raise
    except AdminCompanyProfileValidationError as exc:
        raise AdminCompanyProfileIntegrityError(str(exc)) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AdminCompanyProfileIntegrityError("企业档案审计结构无法解析") from exc


def _append_audit(
    db: Session, *, row: AdminCompanyProfile, action: str, actor_id: str,
    reason: str, before: Optional[Dict[str, Any]], after: Dict[str, Any],
) -> None:
    actor = _required_actor_id(actor_id)
    change_reason = _required_change_reason(reason)
    if action not in {"created", "updated", "archived"}:
        raise AdminCompanyProfileValidationError("审计 action 非法")
    db.add(AdminCompanyProfileAudit(
        profile_id=str(row.id), revision=int(row.revision), action=action,
        actor_id=actor, change_reason=change_reason,
        before_snapshot=_json_copy(before) if before is not None else None,
        after_snapshot=_json_copy(after),
        content_sha256=row.content_sha256,
    ))


def create_company_profile(
    db: Session, write: CompanyProfileWrite, *, actor_id: str, change_reason: str,
) -> AdminCompanyProfile:
    actor = _required_actor_id(actor_id)
    reason = _required_change_reason(change_reason)
    values = prepare_company_profile_content(write)
    now = datetime.utcnow()
    row = AdminCompanyProfile(
        name=values["name"], credit_code=values["credit_code"], status="active", revision=1,
        content_sha256=values["content_sha256"], profile_json=values["profile"],
        scenario=values["scenario"], scenario_data=values["scenario_data"],
        field_sources=values["field_sources"], materials=values["materials"],
        created_by=actor, updated_by=actor,
        created_at=now, updated_at=now,
    )
    db.add(row)
    try:
        db.flush()
        _append_audit(db, row=row, action="created", actor_id=actor,
                      reason=reason, before=None, after=_row_snapshot(row))
        db.flush()
        validate_company_profile_audit_history(db, str(row.id))
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError as exc:
        db.rollback()
        raise AdminCompanyProfileConflict("数据库约束拒绝企业档案写入") from exc
    except Exception:
        db.rollback()
        raise


def _get_row(db: Session, profile_id: str) -> AdminCompanyProfile:
    row = db.query(AdminCompanyProfile).filter(AdminCompanyProfile.id == str(profile_id)).first()
    if row is None:
        raise AdminCompanyProfileNotFound("企业档案不存在")
    return row


def get_company_profile(db: Session, profile_id: str, *, include_archived: bool = False) -> AdminCompanyProfile:
    row = _get_row(db, profile_id)
    if row.status != "active" and not include_archived:
        raise AdminCompanyProfileNotFound("企业档案不存在")
    return row


def list_company_profiles(
    db: Session, *, query: str = "", include_archived: bool = False,
    offset: int = 0, limit: int = 50,
) -> tuple[List[AdminCompanyProfile], int]:
    q = db.query(AdminCompanyProfile)
    if not include_archived:
        q = q.filter(AdminCompanyProfile.status == "active")
    if query.strip():
        needle = f"%{query.strip()}%"
        q = q.filter(or_(AdminCompanyProfile.name.ilike(needle), AdminCompanyProfile.credit_code.ilike(needle)))
    total = q.count()
    rows = q.order_by(AdminCompanyProfile.updated_at.desc(), AdminCompanyProfile.id.desc()).offset(offset).limit(limit).all()
    return rows, total


def update_company_profile(
    db: Session, profile_id: str, write: CompanyProfileWrite, *, expected_revision: int,
    actor_id: str, change_reason: str,
) -> AdminCompanyProfile:
    actor = _required_actor_id(actor_id)
    reason = _required_change_reason(change_reason)
    current = _get_row(db, profile_id)
    if current.status != "active":
        raise AdminCompanyProfileNotFound("企业档案已归档")
    if current.revision != expected_revision:
        raise AdminCompanyProfileConflict(f"档案已被更新；当前 revision={current.revision}")
    validate_company_profile_audit_history(db, profile_id)
    before = _row_snapshot(current)
    values = prepare_company_profile_content(write)
    next_revision = expected_revision + 1
    now = datetime.utcnow()
    try:
        result = db.execute(update(AdminCompanyProfile).where(
            AdminCompanyProfile.id == str(profile_id),
            AdminCompanyProfile.status == "active",
            AdminCompanyProfile.revision == expected_revision,
        ).values(
            name=values["name"], credit_code=values["credit_code"], revision=next_revision,
            content_sha256=values["content_sha256"], profile_json=values["profile"],
            scenario=values["scenario"], scenario_data=values["scenario_data"],
            field_sources=values["field_sources"], materials=values["materials"],
            updated_by=actor, updated_at=now,
        ))
        if result.rowcount != 1:
            raise AdminCompanyProfileConflict("档案已被并发修改，请刷新后重试")
        db.flush()
        updated = _get_row(db, profile_id)
        _append_audit(db, row=updated, action="updated", actor_id=actor,
                      reason=reason, before=before, after=_row_snapshot(updated))
        db.flush()
        validate_company_profile_audit_history(db, profile_id)
        db.commit()
        db.refresh(updated)
        return updated
    except IntegrityError as exc:
        db.rollback()
        raise AdminCompanyProfileConflict("数据库约束拒绝企业档案写入") from exc
    except Exception:
        db.rollback()
        raise


def archive_company_profile(
    db: Session, profile_id: str, *, expected_revision: int, actor_id: str,
    change_reason: str,
) -> AdminCompanyProfile:
    actor = _required_actor_id(actor_id)
    reason = _required_change_reason(change_reason)
    current = _get_row(db, profile_id)
    if current.status != "active":
        raise AdminCompanyProfileNotFound("企业档案不存在或已归档")
    if current.revision != expected_revision:
        raise AdminCompanyProfileConflict(f"档案已被更新；当前 revision={current.revision}")
    validate_company_profile_audit_history(db, profile_id)
    before = _row_snapshot(current)
    now = datetime.utcnow()
    try:
        result = db.execute(update(AdminCompanyProfile).where(
            AdminCompanyProfile.id == str(profile_id),
            AdminCompanyProfile.status == "active",
            AdminCompanyProfile.revision == expected_revision,
        ).values(
            status="archived", revision=expected_revision + 1,
            archived_at=now, archived_by=actor, updated_at=now, updated_by=actor,
        ))
        if result.rowcount != 1:
            raise AdminCompanyProfileConflict("档案已被并发修改，请刷新后重试")
        db.flush()
        archived = _get_row(db, profile_id)
        _append_audit(db, row=archived, action="archived", actor_id=actor,
                      reason=reason, before=before, after=_row_snapshot(archived))
        db.flush()
        validate_company_profile_audit_history(db, profile_id)
        db.commit()
        db.refresh(archived)
        return archived
    except IntegrityError as exc:
        db.rollback()
        raise AdminCompanyProfileConflict("数据库约束拒绝企业档案写入") from exc
    except Exception:
        db.rollback()
        raise


def list_company_profile_history(db: Session, profile_id: str) -> List[AdminCompanyProfileAudit]:
    return list(reversed(validate_company_profile_audit_history(db, profile_id)))


def _lexical_tokens(value: str) -> List[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", value.lower())
    return [token for token in tokens if token]


def search_profile_materials(
    db: Session, profile_id: str, *, query: str, limit: int,
    include_archived: bool = False,
) -> Dict[str, Any]:
    """Deterministic, scoreless lexical retrieval over only this profile's materials."""
    row = get_company_profile(db, profile_id, include_archived=include_archived)
    tokens = _lexical_tokens(query)
    if not tokens:
        raise AdminCompanyProfileValidationError("检索词必须含有可检索字符")
    results: List[Dict[str, Any]] = []
    for material in row.materials or []:
        haystack = " ".join(str(material.get(key) or "") for key in ("title", "content", "reference")).lower()
        if all(token in haystack for token in tokens):
            results.append({
                "material_id": material.get("material_id"),
                "source_type": material.get("source_type"),
                "title": material.get("title"),
                "content": material.get("content"),
                "reference": material.get("reference"),
                "as_of_date": material.get("as_of_date"),
                "date_unknown_reason": material.get("date_unknown_reason"),
                "eligible_for_structured_evidence": False,
            })
        if len(results) >= limit:
            break
    return {"items": results, "total": len(results)}


def get_active_profile_snapshot(db: Session, profile_id: str) -> Dict[str, Any]:
    """Return the JSON-only, integrity-checked snapshot consumed by the run-time layer."""
    row = get_company_profile(db, profile_id, include_archived=False)
    validate_company_profile_audit_history(db, profile_id)
    # Re-run strict persistence validation to avoid making a tampered DB row a
    # source of an apparently valid no-record conclusion.
    try:
        values = _profile_snapshot_values(
            row.profile_json, row.scenario or "", row.scenario_data or {},
            row.field_sources or [], row.materials or [],
        )
        if _content_sha256(values) != row.content_sha256:
            raise AdminCompanyProfileIntegrityError("企业档案内容哈希不匹配，拒绝作为尽调输入")

        class _StoredWrite:
            profile = values["profile"]
            scenario = values["scenario"]
            scenario_data = values["scenario_data"]
            field_sources = values["field_sources"]
            materials = values["materials"]
        checked = prepare_company_profile_content(_StoredWrite())
    except AdminCompanyProfileIntegrityError:
        raise
    except AdminCompanyProfileValidationError as exc:
        raise AdminCompanyProfileIntegrityError(f"企业档案内容无法通过严格校验：{exc}") from exc
    except Exception as exc:
        raise AdminCompanyProfileIntegrityError("企业档案内容无法通过严格校验") from exc
    if checked["content_sha256"] != row.content_sha256:
        raise AdminCompanyProfileIntegrityError("企业档案规范化后内容哈希不匹配")

    ref = {
        "id": str(row.id), "revision": int(row.revision),
        "content_sha256": row.content_sha256, "source": "admin_company_profile",
    }
    profile = copy.deepcopy(values["profile"])
    profile["_admin_profile_ref"] = copy.deepcopy(ref)
    profile["_scenario_data"] = copy.deepcopy(values["scenario_data"])
    profile["_admin_field_sources"] = copy.deepcopy(values["field_sources"])
    return {"profile": profile, "ref": ref, "scenario": values["scenario"]}


def company_profile_templates() -> Dict[str, Any]:
    """Return the checklist-derived, front-end consumable profile template.

    ``core`` and ``scenarios`` are deliberately derived from the authoritative
    due-diligence checklists instead of mirroring their field IDs here.  This
    keeps the administrative input UI aligned when the fixed checklist gains a
    field or a business scenario.
    """
    def template_field(item: Any, scope: str) -> Dict[str, Any]:
        return {
            "field_id": item.field_id,
            "field_name": item.field_name,
            "category": item.category,
            "required": item.required,
            "description": item.description,
            "scope": scope,
            # Input semantics live in the server template so clients do not
            # duplicate scenario field lists or silently change number values
            # into strings after an edit round-trip.
            "input_type": "number" if item.field_id in SCENARIO_NUMBER_INPUT_IDS else "text",
        }

    core = [template_field(item, "core") for item in CHECKLIST]
    scenarios = {
        name: [template_field(item, f"scenario:{name}") for item in items]
        for name, items in SCENARIO_CHECKLISTS.items()
    }
    return {
        "version": "company-profile-template-v1",
        "core": core,
        "scenarios": scenarios,
        # Legacy template hints remain available for existing consumers.  New
        # clients should render the checklist from ``core`` and ``scenarios``.
        "profile": {"name": "企业名称", "credit_code": "", "registration": {}, "financials": []},
        "scenario_options": ["", *SCENARIO_CHECKLISTS],
        "scenario_data_keys": {
            scenario: [item.field_id for item in items]
            for scenario, items in SCENARIO_CHECKLISTS.items()
        },
        "field_source": {
            "source_id": "source-001", "name": "来源名称", "issuer": "出具机构",
            "source_type": "official", "field_ids": [core[0]["field_id"]] if core else [],
            "retrieved_at": "2026-08-28T00:00:00", "as_of_date": "2026-08-28",
            "reference": "可追溯引用", "sha256": None,
        },
        "material": {
            "source_type": "company_submitted", "title": "材料标题", "content": "材料正文",
            "as_of_date": "2026-08-28", "date_unknown_reason": None,
        },
        "coverage": "由 field_sources 自动重建；客户端传入值不会被保存",
        "required": "不接受自定义 required；必查项由服务端固定清单定义",
    }
