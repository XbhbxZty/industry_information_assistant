"""Read-only key-reference inventory and retirement preflight for one database.

This does not modify environment configuration, delete keys, or rewrite seals.
Opaque LangGraph history is reported conservatively, never deserialized here.
"""
from __future__ import annotations

from collections import Counter
import os
import re
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from core.company_profile_audit_keys import (
    CompanyProfileAuditKeyConfigurationError, load_company_profile_audit_keyring,
)
from core.checkpoint_keys import CheckpointKeyConfigurationError, load_checkpoint_keyring
from core.schema_head_guard import assert_database_schema_at_head
from models.company_profile import (
    AdminCompanyProfile, AdminCompanyProfileAudit, AdminCompanyProfileAuditAnchor,
)
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity
from service.admin_company_profile_service import (
    AdminCompanyProfileValidationError, validate_company_profile_audit_history,
)
from service.checkpoint_integrity import (
    GRAPH_SEAL_FIELD, describe_checkpoint_verification_keys,
)
from service.checkpoint_service import CheckpointService
from service.deep_research_v2.state import verify_admin_profile_snapshot_binding


_AUDIT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CHECKPOINT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_LANGGRAPH_HISTORY_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _requested_ids(values: Iterable[str], pattern: re.Pattern) -> list[str]:
    result = set(values)
    if any(not isinstance(value, str) or pattern.fullmatch(value) is None for value in result):
        raise ValueError("invalid retirement key identifier")
    return sorted(result)


def _collect(db: Session, retire_audit: list[str], retire_checkpoint: list[str]) -> dict[str, Any]:
    issues: Counter[str] = Counter()
    references: dict[str, dict[str, Counter]] = {"audit": {}, "checkpoint": {}}
    unknown: dict[str, set[str]] = {"audit": set(), "checkpoint": set()}
    audit_ring = checkpoint_ring = None
    checkpoint_fingerprints = {}
    try:
        audit_ring = load_company_profile_audit_keyring()
    except CompanyProfileAuditKeyConfigurationError:
        if any(name in os.environ for name in ("COMPANY_PROFILE_AUDIT_KEYS_JSON", "COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID")):
            issues["audit_key_configuration_invalid"] += 1
    try:
        checkpoint_ring = load_checkpoint_keyring()
        checkpoint_fingerprints = describe_checkpoint_verification_keys()
    except (CheckpointKeyConfigurationError, ValueError):
        if any(name in os.environ for name in ("RESEARCH_CHECKPOINT_KEYS_JSON", "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", "RESEARCH_CHECKPOINT_LEGACY_KEY_ID")):
            issues["checkpoint_key_configuration_invalid"] += 1

    def add(namespace: str, key_id: Any, source: str, *, fingerprint: bool = False):
        pattern = _FINGERPRINT if fingerprint else (_AUDIT_ID if namespace == "audit" else _CHECKPOINT_ID)
        if not isinstance(key_id, str) or pattern.fullmatch(key_id) is None:
            issues[f"{namespace}_invalid_key_identifier"] += 1
            return
        ring = audit_ring if namespace == "audit" else checkpoint_ring
        alias = checkpoint_fingerprints.get(key_id) if fingerprint else key_id
        if alias is None or ring is None or alias not in ring.key_ids:
            unknown[namespace].add(key_id)
            return
        references[namespace].setdefault(alias, Counter())[source] += 1

    audits = db.query(AdminCompanyProfileAudit).all()
    anchors = db.query(AdminCompanyProfileAuditAnchor).all()
    profiles = db.query(AdminCompanyProfile).all()
    profile_ids = {row.id for row in profiles}
    unsigned_audits = 0
    for row in audits:
        if row.audit_mac is None and row.key_id is None:
            unsigned_audits += 1
        else:
            add("audit", row.key_id, "audits")
        if row.profile_id not in profile_ids:
            issues["orphan_audit"] += 1
    for row in anchors:
        add("audit", row.key_id, "anchors")
    if (profiles or audits or anchors or retire_audit) and audit_ring is None:
        issues["audit_key_configuration_unavailable"] += 1
    if audit_ring is not None:
        for row in profiles:
            try:
                validate_company_profile_audit_history(db, row.id)
            except AdminCompanyProfileValidationError:
                issues["audit_history_verification_failed"] += 1

    checkpoints = db.query(ResearchCheckpoint).all()
    integrities = db.query(ResearchCheckpointIntegrity).all()
    paired = {row.session_id: row for row in integrities}
    checkpoint_ids = {str(row.id) for row in checkpoints}
    session_counts = Counter(row.session_id for row in checkpoints)
    issues["duplicate_checkpoint_session"] += sum(count - 1 for count in session_counts.values())
    for integrity in integrities:
        add("checkpoint", integrity.key_id, "business", fingerprint=True)
        if integrity.checkpoint_id not in checkpoint_ids:
            issues["orphan_checkpoint_integrity"] += 1
    if (checkpoints or integrities or retire_checkpoint) and checkpoint_ring is None:
        issues["checkpoint_key_configuration_unavailable"] += 1
    unsigned_checkpoints = legacy_bindings = 0
    service = CheckpointService()
    for checkpoint in checkpoints:
        state = checkpoint.state_json
        if not isinstance(state, dict):
            issues["checkpoint_state_invalid"] += 1
            continue
        graph_seal = state.get(GRAPH_SEAL_FIELD)
        if isinstance(graph_seal, dict) and graph_seal:
            add("checkpoint", graph_seal.get("key_id"), "graph", fingerprint=True)
        binding = state.get("admin_profile_snapshot_binding")
        if binding:
            if isinstance(binding, dict) and binding.get("version") == 2:
                legacy_bindings += 1
                if checkpoint_ring is None or checkpoint_ring.legacy_key_id is None:
                    issues["legacy_snapshot_key_unavailable"] += 1
                else:
                    add("checkpoint", checkpoint_ring.legacy_key_id, "snapshot_v2")
            elif isinstance(binding, dict) and binding.get("version") == 3:
                add("checkpoint", binding.get("key_id"), "snapshot_v3")
            else:
                issues["snapshot_binding_invalid"] += 1
            if checkpoint_ring is not None:
                try:
                    verify_admin_profile_snapshot_binding(
                        binding, checkpoint.session_id, state.get("provided_company_profile"),
                        state.get("admin_profile_ref"), state.get("admin_profile_scenario", ""),
                    )
                except ValueError:
                    issues["snapshot_binding_verification_failed"] += 1
        integrity = paired.get(checkpoint.session_id)
        if integrity is None:
            unsigned_checkpoints += 1
            if graph_seal or binding:
                issues["checkpoint_integrity_missing"] += 1
        elif checkpoint_ring is not None:
            try:
                service._verify_integrity_pair(checkpoint, integrity, checkpoint.session_id)
            except ValueError:
                issues["checkpoint_verification_failed"] += 1

    opaque_history = []
    for table in _LANGGRAPH_HISTORY_TABLES:
        exists = db.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}).scalar()
        if exists is not None:
            # Table names are a fixed server-side tuple, not user input.
            has_rows = db.execute(text(f"SELECT EXISTS (SELECT 1 FROM public.{table})")).scalar()
            if has_rows:
                opaque_history.append(table)

    requested = {"audit": retire_audit, "checkpoint": retire_checkpoint}
    blocked = []
    for namespace, ring in (("audit", audit_ring), ("checkpoint", checkpoint_ring)):
        for key_id in requested[namespace]:
            reasons = []
            if ring is None or key_id not in ring.key_ids:
                reasons.append("not_configured")
            if ring is not None and key_id == ring.active_key_id:
                reasons.append("active_signing_key")
            if references[namespace].get(key_id):
                reasons.append("retained_records_reference_key")
            if namespace == "checkpoint" and opaque_history:
                reasons.append("opaque_langgraph_history_not_proven_key_free")
            if reasons:
                blocked.append({"namespace": namespace, "key_id": key_id, "reasons": reasons})
    for namespace, missing in unknown.items():
        if missing:
            issues[f"{namespace}_unknown_key_references"] += len(missing)
    issues = {key: count for key, count in sorted(issues.items()) if count}
    return {
        "version": 1, "scope": "selected_database_only", "read_only": True,
        "preflight_passed": not issues and not blocked,
        "configuration": {
            "audit": {"active_key_id": audit_ring.active_key_id if audit_ring else None,
                      "key_ids": sorted(audit_ring.key_ids) if audit_ring else []},
            "checkpoint": {"active_key_id": checkpoint_ring.active_key_id if checkpoint_ring else None,
                           "key_ids": sorted(checkpoint_ring.key_ids) if checkpoint_ring else [],
                           "legacy_key_id": checkpoint_ring.legacy_key_id if checkpoint_ring else None},
        },
        "references": {ns: {key: dict(counts) for key, counts in sorted(values.items())}
                       for ns, values in references.items()},
        "unknown_key_references": {ns: sorted(values) for ns, values in unknown.items()},
        "legacy": {"unsigned_audits": unsigned_audits, "unsigned_checkpoints": unsigned_checkpoints,
                   "snapshot_v2_bindings": legacy_bindings},
        "opaque_langgraph_history": opaque_history,
        "retirement": {"requested": requested, "blocked": blocked},
        "issues": issues,
        "limitations": ["Backups, other databases, remote workers and in-memory runs are not inspected.",
                        "Stop writers before applying configuration; this read-only result is not a lock or authorization."],
    }


def inspect_key_rotation(
    engine: Engine, *, retire_audit_keys: Iterable[str] = (), retire_checkpoint_keys: Iterable[str] = (),
) -> dict[str, Any]:
    audit_ids = _requested_ids(retire_audit_keys, _AUDIT_ID)
    checkpoint_ids = _requested_ids(retire_checkpoint_keys, _CHECKPOINT_ID)
    if engine.dialect.name != "postgresql":
        raise ValueError("key rotation preflight requires PostgreSQL")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path TO public")
            assert_database_schema_at_head(connection)
            with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as db:
                report = _collect(db, audit_ids, checkpoint_ids)
                report["database"] = connection.exec_driver_sql("SELECT current_database()").scalar()
                return report
