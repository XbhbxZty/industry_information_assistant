"""管理员维护的企业尽调档案与不可变修订审计记录。

这两张表刻意不关联个人知识库：档案是全局、结构化的尽调输入，
不是用户上传文档，也不会产生 Milvus collection。

使用 ``String`` UUID 与 SQLAlchemy 通用 ``JSON``，让 SQLite 回归测试和
PostgreSQL 生产库共享同一模型定义。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.types import JSON

from core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AdminCompanyProfile(Base):
    """当前生效版本；历史版本只写入审计表，不在本表保留可变副本。"""

    __tablename__ = "admin_company_profiles"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False, index=True)
    credit_code = Column(String(64), nullable=True, unique=True, index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    revision = Column(Integer, nullable=False, default=1)
    content_sha256 = Column(String(64), nullable=False)

    # Canonical company profile and stage-3 sidecar data.  Do not use JSONB here:
    # this model is deliberately exercised against SQLite in deterministic tests.
    profile_json = Column(JSON, nullable=False)
    scenario = Column(String(32), nullable=False, default="")
    scenario_data = Column(JSON, nullable=False, default=dict)
    field_sources = Column(JSON, nullable=False, default=list)
    materials = Column(JSON, nullable=False, default=list)

    # Store actor ids as strings.  The existing User primary key is PostgreSQL-
    # specific UUID, whereas this table must be independently creatable in SQLite.
    created_by = Column(String(64), nullable=True, index=True)
    updated_by = Column(String(64), nullable=True, index=True)
    archived_by = Column(String(64), nullable=True, index=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AdminCompanyProfileAudit(Base):
    """Append-only full before/after snapshots for each profile revision."""

    __tablename__ = "admin_company_profile_audits"

    id = Column(String(36), primary_key=True, default=_uuid)
    profile_id = Column(String(36), nullable=False, index=True)
    revision = Column(Integer, nullable=False, index=True)
    action = Column(String(16), nullable=False)  # created / updated / archived
    actor_id = Column(String(64), nullable=True, index=True)
    change_reason = Column(Text, nullable=False, default="")
    before_snapshot = Column(JSON, nullable=True)
    after_snapshot = Column(JSON, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
