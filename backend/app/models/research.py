# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""研究检查点模型"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from core.database import Base


def _utc_now() -> datetime:
    """Return an aware UTC value for review-claim lifecycle timestamps."""
    return datetime.now(timezone.utc)


class ResearchCheckpoint(Base):
    """研究检查点模型 - 用于保存和恢复深度研究状态"""
    __tablename__ = "research_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'paused', 'completed', 'failed')",
            name="ck_research_checkpoints_status",
        ),
        UniqueConstraint("session_id", name="uq_research_checkpoints_session_id"),
        # PostgreSQL requires a unique target for the integrity pair FK, even
        # though ``id`` itself is already the primary key.
        UniqueConstraint(
            "id", "session_id", name="uq_research_checkpoints_id_session_id",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(64), index=True, nullable=False)  # 研究会话 ID
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    query = Column(Text, nullable=False)  # 原始查询
    phase = Column(String(32), nullable=False)  # planning/researching/analyzing/writing/reviewing/completed
    iteration = Column(Integer, default=0)  # 当前迭代次数
    state_json = Column(JSONB, nullable=False)  # 完整的 ResearchState（后端状态）
    ui_state_json = Column(JSONB)  # 前端 UI 状态（研究步骤、搜索结果、图表等）
    final_report = Column(Text)  # 最终报告内容
    status = Column(String(16), default="running", nullable=False)  # running/paused/completed/failed
    error_message = Column(Text)  # 错误信息（如果失败）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    user = relationship("User", backref="research_checkpoints")

    def to_dict(self, include_state: bool = False):
        """转换为字典"""
        result = {
            "id": str(self.id),
            "session_id": self.session_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "query": self.query,
            "phase": self.phase,
            "iteration": self.iteration,
            "status": self.status,
            "error_message": self.error_message,
            "final_report": self.final_report,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_state:
            result["state_json"] = self.state_json
            result["ui_state_json"] = self.ui_state_json
        return result


class ResearchCheckpointIntegrity(Base):
    """One-to-one integrity metadata bound to its persisted checkpoint."""
    __tablename__ = "research_checkpoint_integrities"
    __table_args__ = (
        CheckConstraint(
            "business_revision >= 1",
            name="ck_research_checkpoint_integrities_business_revision",
        ),
        UniqueConstraint(
            "checkpoint_id", name="uq_research_checkpoint_integrities_checkpoint_id",
        ),
        ForeignKeyConstraint(
            ["checkpoint_id", "session_id"],
            ["research_checkpoints.id", "research_checkpoints.session_id"],
            name="fk_research_checkpoint_integrities_checkpoint_session",
            ondelete="CASCADE",
        ),
    )

    session_id = Column(String(64), primary_key=True)
    checkpoint_id = Column(UUID(as_uuid=True), nullable=False)
    mode = Column(String(32), nullable=False)
    integrity_version = Column(Integer, nullable=False)
    key_id = Column(String(64), nullable=False)
    business_revision = Column(Integer, nullable=False)
    business_seal = Column(String(64), nullable=False)
    context_seal = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchReviewClaim(Base):
    """Durable ownership of the next human review decision for a checkpoint."""
    __tablename__ = "research_review_claims"
    __table_args__ = (
        UniqueConstraint("token", name="uq_research_review_claims_token"),
        CheckConstraint(
            "basis_version >= 1",
            name="ck_research_review_claims_basis_version",
        ),
        CheckConstraint(
            "owner_id <> reviewer_id",
            name="ck_research_review_claims_owner_not_reviewer",
        ),
        CheckConstraint(
            "state IN ('claimed', 'accepted', 'finalized', 'released')",
            name="ck_research_review_claims_state",
        ),
        CheckConstraint(
            "("
            "state IN ('claimed', 'released') "
            "AND decision_digest IS NULL AND decision_json IS NULL "
            "AND accepted_at IS NULL AND finalized_at IS NULL"
            ") OR ("
            "state = 'accepted' "
            "AND decision_digest IS NOT NULL AND decision_json IS NOT NULL "
            "AND accepted_at IS NOT NULL AND finalized_at IS NULL"
            ") OR ("
            "state = 'finalized' "
            "AND decision_digest IS NOT NULL AND decision_json IS NOT NULL "
            "AND accepted_at IS NOT NULL AND finalized_at IS NOT NULL"
            ")",
            name="ck_research_review_claims_state_shape",
        ),
    )

    checkpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_checkpoints.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    owner_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    reviewer_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    token = Column(String(64), nullable=False)
    basis_version = Column(Integer, nullable=False)
    basis_seal = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False)
    lease_expires_at = Column(DateTime(timezone=True), nullable=False)
    decision_digest = Column(String(64), nullable=True)
    # Python None means no accepted decision, not the JSON literal null.
    decision_json = Column(JSONB(none_as_null=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now,
    )
