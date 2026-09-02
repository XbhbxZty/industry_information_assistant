"""管理员维护的企业尽调档案与不可变修订审计记录。

这三张表刻意不关联个人知识库：档案是全局、结构化的尽调输入，
不是用户上传文档，也不会产生 Milvus collection。

使用 ``String`` UUID 与 SQLAlchemy 通用 ``JSON``，让 SQLite 回归测试和
PostgreSQL 生产库共享同一模型定义。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import JSON

from core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AdminCompanyProfile(Base):
    """当前生效版本；历史版本只写入审计表，不在本表保留可变副本。"""

    __tablename__ = "admin_company_profiles"
    __table_args__ = (
        CheckConstraint(
            "length(trim(created_by)) BETWEEN 1 AND 64",
            name="ck_company_profile_created_by_nonblank",
        ),
        CheckConstraint(
            "length(trim(updated_by)) BETWEEN 1 AND 64",
            name="ck_company_profile_updated_by_nonblank",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False, index=True)
    credit_code = Column(String(64), nullable=True, unique=True, index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    revision = Column(Integer, nullable=False, default=1)
    content_sha256 = Column(String(64), nullable=False)
    # These heads remain nullable so pre-D2b rows can be represented faithfully.
    # Legacy history is anchored by ``AdminCompanyProfileAuditAnchor`` rather than
    # being retroactively signed.
    audit_head_mac = Column(String(64), nullable=True)
    audit_head_snapshot_sha256 = Column(String(64), nullable=True)

    # Canonical company profile and stage-3 sidecar data.  Do not use JSONB here:
    # this model is deliberately exercised against SQLite in deterministic tests.
    profile_json = Column(JSON, nullable=False)
    scenario = Column(String(32), nullable=False, default="")
    scenario_data = Column(JSON, nullable=False, default=dict)
    field_sources = Column(JSON, nullable=False, default=list)
    materials = Column(JSON, nullable=False, default=list)

    # Store actor ids as strings.  The existing User primary key is PostgreSQL-
    # specific UUID, whereas this table must be independently creatable in SQLite.
    created_by = Column(String(64), nullable=False, index=True)
    updated_by = Column(String(64), nullable=False, index=True)
    archived_by = Column(String(64), nullable=True, index=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AdminCompanyProfileAudit(Base):
    """Append-only full before/after snapshots for each profile revision."""

    __tablename__ = "admin_company_profile_audits"
    __table_args__ = (
        UniqueConstraint("profile_id", "revision", name="uq_company_profile_audit_revision"),
        CheckConstraint("revision >= 1", name="ck_company_profile_audit_revision_positive"),
        CheckConstraint(
            "length(trim(actor_id)) BETWEEN 1 AND 64",
            name="ck_company_profile_audit_actor_nonblank",
        ),
        CheckConstraint(
            "length(trim(change_reason)) BETWEEN 1 AND 500",
            name="ck_company_profile_audit_reason_nonblank",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    profile_id = Column(String(36), nullable=False, index=True)
    revision = Column(Integer, nullable=False, index=True)
    action = Column(String(16), nullable=False)  # created / updated / archived
    actor_id = Column(String(64), nullable=False, index=True)
    change_reason = Column(Text, nullable=False)
    before_snapshot = Column(JSON, nullable=True)
    after_snapshot = Column(JSON, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    # All seal fields are nullable for the legacy, pre-chain audit rows.  New
    # writes populate the complete set together in the service layer.
    integrity_version = Column(Integer, nullable=True)
    integrity_algorithm = Column(String(32), nullable=True)
    key_id = Column(String(128), nullable=True)
    before_snapshot_sha256 = Column(String(64), nullable=True)
    after_snapshot_sha256 = Column(String(64), nullable=True)
    previous_audit_mac = Column(String(64), nullable=True)
    audit_mac = Column(String(64), nullable=True)
    chain_start = Column(String(16), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AdminCompanyProfileAuditAnchor(Base):
    """Migration-time seal for a profile's unsigned, legacy audit history."""

    __tablename__ = "admin_company_profile_audit_anchors"

    profile_id = Column(
        String(36),
        ForeignKey("admin_company_profiles.id"),
        primary_key=True,
    )
    integrity_version = Column(Integer, nullable=False)
    algorithm = Column(String(32), nullable=False)
    key_id = Column(String(128), nullable=False)
    legacy_cutover_revision = Column(Integer, nullable=False)
    legacy_chain_sha256 = Column(String(64), nullable=False)
    legacy_terminal_snapshot_sha256 = Column(String(64), nullable=False)
    anchored_at = Column(DateTime, nullable=False)
    migration_run_id = Column(String(128), nullable=False)
    anchor_mac = Column(String(64), nullable=False)
