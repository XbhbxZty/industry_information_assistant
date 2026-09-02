"""Explicit migration-time anchoring; never called by ordinary profile reads.

The caller owns the transaction. A legacy anchor certifies the complete history
observed at upgrade, NOT the authenticity of events before that upgrade.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from models.company_profile import (
    AdminCompanyProfile, AdminCompanyProfileAudit, AdminCompanyProfileAuditAnchor,
)
from service.admin_company_profile_service import (
    AdminCompanyProfileIntegrityError, _audit_is_legacy, _legacy_audit_record,
    _load_audit_keyring, _row_snapshot, _validate_structural_audit_history,
    validate_company_profile_audit_history,
)
from service.company_profile_audit_integrity import (
    canonical_utc_timestamp, issue_legacy_anchor, legacy_history_sha256, snapshot_sha256,
)


def backfill_company_profile_audit_anchors(
    connection: Connection, *, migration_run_id: str,
) -> int:
    """Validate first, anchor once, flush only; any failure aborts Alembic upgrade."""
    if not connection.in_transaction():
        raise RuntimeError("审计锚定必须在调用方的迁移事务内运行")
    # rollback_only prevents a Session close/commit from committing or rolling
    # back the connection's outer migration transaction. Exceptions propagate.
    with Session(bind=connection, join_transaction_mode="rollback_only") as db:
        orphan = db.query(AdminCompanyProfileAudit.id).outerjoin(
            AdminCompanyProfile, AdminCompanyProfileAudit.profile_id == AdminCompanyProfile.id,
        ).filter(AdminCompanyProfile.id.is_(None)).first()
        if orphan is not None:
            raise AdminCompanyProfileIntegrityError("发现无对应企业档案的旧审计；拒绝自动锚定")
        profiles = db.query(AdminCompanyProfile).order_by(AdminCompanyProfile.id).with_for_update().all()
        if not profiles:
            return 0  # A fresh empty install does not need signing configuration.
        keyring = _load_audit_keyring()
        anchored = 0
        for row in profiles:
            audits = _validate_structural_audit_history(db, str(row.id))
            anchor = db.get(AdminCompanyProfileAuditAnchor, str(row.id))
            if anchor is not None or any(not _audit_is_legacy(item) for item in audits):
                # Safe retry: validate, never replace an existing seal/anchor.
                validate_company_profile_audit_history(db, str(row.id))
                continue
            if row.audit_head_mac is not None or row.audit_head_snapshot_sha256 is not None:
                raise AdminCompanyProfileIntegrityError("无签名旧历史携带非空链头；拒绝自动锚定")
            now = datetime.utcnow()
            record = issue_legacy_anchor(
                keyring=keyring, profile_id=str(row.id), legacy_cutover_revision=row.revision,
                legacy_chain_sha256=legacy_history_sha256([_legacy_audit_record(item) for item in audits]),
                legacy_terminal_snapshot_sha256=snapshot_sha256(_row_snapshot(row)),
                anchored_at=canonical_utc_timestamp(now), migration_run_id=migration_run_id,
            )
            values = {key: value for key, value in record.items() if key not in {"format", "anchored_at"}}
            db.add(AdminCompanyProfileAuditAnchor(**values, anchored_at=now))
            row.audit_head_mac = record["anchor_mac"]
            row.audit_head_snapshot_sha256 = record["legacy_terminal_snapshot_sha256"]
            db.flush()
            validate_company_profile_audit_history(db, str(row.id))
            anchored += 1
        return anchored
