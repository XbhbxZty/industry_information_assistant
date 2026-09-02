"""Bind checkpoint integrity rows to checkpoint identity and review claims.

Revision ID: 20260902_0003
Revises: 20260902_0002

This is deliberately an online PostgreSQL migration.  It observes and seals
legacy rows only after rejecting every ambiguous or damaged relationship; it
never manufactures a repaired history.
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260902_0003"
down_revision: Union[str, None] = "20260902_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REVISION_FUNCTION = "reject_research_checkpoint_integrity_revision_rollback"
_REVISION_TRIGGER = "research_checkpoint_integrities_revision_monotonic"
_UUID_PATTERN = (
    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _reject_if_rows(bind, statement: str, reason: str) -> None:
    """Fail the enclosing Alembic transaction before changing legacy data."""
    if bind.execute(sa.text(statement)).first() is not None:
        raise RuntimeError(f"检查点上下文迁移前置校验失败：{reason}")


def _preflight_legacy_rows(bind) -> None:
    """Reject all legacy shapes which cannot be made unambiguous by D4a."""
    _reject_if_rows(bind, """
        SELECT 1 FROM public.research_checkpoints
        GROUP BY session_id HAVING count(*) > 1
    """, "research_checkpoints.session_id 重复")
    _reject_if_rows(bind, """
        SELECT 1 FROM public.research_checkpoints
        WHERE status IS NULL
           OR status NOT IN ('running', 'paused', 'completed', 'failed')
    """, "research_checkpoints.status 为空或非法")
    _reject_if_rows(bind, f"""
        SELECT 1 FROM public.research_checkpoint_integrities
        WHERE checkpoint_id IS NULL
           OR checkpoint_id !~* '{_UUID_PATTERN}'
    """, "完整性 checkpoint_id 不能严格转换为 UUID")
    _reject_if_rows(bind, """
        SELECT 1 FROM public.research_checkpoint_integrities
        WHERE business_revision IS NULL OR business_revision < 1
    """, "完整性 business_revision 非法")
    _reject_if_rows(bind, """
        SELECT 1
        FROM public.research_checkpoints AS checkpoint
        LEFT JOIN public.research_checkpoint_integrities AS integrity
          ON integrity.session_id = checkpoint.session_id
        WHERE integrity.session_id IS NULL
    """, "检查点缺少完整性记录")
    _reject_if_rows(bind, """
        SELECT 1
        FROM public.research_checkpoint_integrities AS integrity
        LEFT JOIN public.research_checkpoints AS checkpoint
          ON checkpoint.session_id = integrity.session_id
        WHERE checkpoint.id IS NULL
           OR checkpoint.id <> integrity.checkpoint_id::uuid
    """, "完整性记录孤儿或 checkpoint_id/session_id 错配")
    _reject_if_rows(bind, """
        SELECT 1 FROM public.research_checkpoint_integrities
        GROUP BY checkpoint_id::uuid HAVING count(*) > 1
    """, "完整性 checkpoint_id 重复")


def _install_postgresql_revision_guard() -> None:
    op.execute(f"""
        CREATE FUNCTION public.{_REVISION_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.business_revision <= OLD.business_revision THEN
                RAISE EXCEPTION 'research checkpoint business_revision must increase'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER {_REVISION_TRIGGER}
        BEFORE UPDATE ON public.research_checkpoint_integrities
        FOR EACH ROW EXECUTE FUNCTION public.{_REVISION_FUNCTION}();
    """)


def _drop_postgresql_revision_guard() -> None:
    op.execute(
        f"DROP TRIGGER IF EXISTS {_REVISION_TRIGGER} "
        "ON public.research_checkpoint_integrities;"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{_REVISION_FUNCTION}();")


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("检查点上下文迁移需要在线升级以校验并封签旧数据")

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("检查点上下文迁移仅支持在线 PostgreSQL 升级")

    # All legacy reads and DDL are protected from concurrent writers.  The
    # preflight is intentionally first: it aborts rather than repairing data.
    op.execute(
        "LOCK TABLE public.research_checkpoints, "
        "public.research_checkpoint_integrities IN ACCESS EXCLUSIVE MODE"
    )
    _preflight_legacy_rows(bind)

    op.create_unique_constraint(
        "uq_research_checkpoints_session_id", "research_checkpoints", ["session_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_research_checkpoints_id_session_id", "research_checkpoints",
        ["id", "session_id"], schema="public",
    )
    op.create_check_constraint(
        "ck_research_checkpoints_status", "research_checkpoints",
        "status IN ('running', 'paused', 'completed', 'failed')", schema="public",
    )
    op.alter_column("research_checkpoints", "status", nullable=False, schema="public")

    op.add_column(
        "research_checkpoint_integrities",
        sa.Column("context_seal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="public",
    )
    op.execute("""
        ALTER TABLE public.research_checkpoint_integrities
        ALTER COLUMN checkpoint_id TYPE uuid USING checkpoint_id::uuid
    """)
    op.create_unique_constraint(
        "uq_research_checkpoint_integrities_checkpoint_id",
        "research_checkpoint_integrities", ["checkpoint_id"], schema="public",
    )
    op.create_check_constraint(
        "ck_research_checkpoint_integrities_business_revision",
        "research_checkpoint_integrities", "business_revision >= 1", schema="public",
    )
    op.create_foreign_key(
        "fk_research_checkpoint_integrities_checkpoint_session",
        "research_checkpoint_integrities", "research_checkpoints",
        ["checkpoint_id", "session_id"], ["id", "session_id"],
        source_schema="public", referent_schema="public", ondelete="CASCADE",
    )

    # The helper verifies every old graph/business seal and issues only the
    # separately-domain-bound migration observation seal.  It must execute in
    # this caller-owned transaction so a single bad row rolls back all DDL.
    from service.checkpoint_context_backfill import backfill_checkpoint_contexts

    op.execute("SELECT set_config('search_path', 'public', true)")
    backfill_checkpoint_contexts(bind)
    op.alter_column(
        "research_checkpoint_integrities", "context_seal", nullable=False, schema="public",
    )

    op.create_table(
        "research_review_claims",
        sa.Column("checkpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("basis_version", sa.Integer(), nullable=False),
        sa.Column("basis_seal", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_digest", sa.String(length=64), nullable=True),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text(), none_as_null=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("basis_version >= 1", name="ck_research_review_claims_basis_version"),
        sa.CheckConstraint("owner_id <> reviewer_id", name="ck_research_review_claims_owner_not_reviewer"),
        sa.CheckConstraint(
            "state IN ('claimed', 'accepted', 'finalized', 'released')",
            name="ck_research_review_claims_state",
        ),
        sa.CheckConstraint(
            "(state IN ('claimed', 'released') AND decision_digest IS NULL "
            "AND decision_json IS NULL AND accepted_at IS NULL AND finalized_at IS NULL) "
            "OR (state = 'accepted' AND decision_digest IS NOT NULL "
            "AND decision_json IS NOT NULL AND accepted_at IS NOT NULL AND finalized_at IS NULL) "
            "OR (state = 'finalized' AND decision_digest IS NOT NULL "
            "AND decision_json IS NOT NULL AND accepted_at IS NOT NULL AND finalized_at IS NOT NULL)",
            name="ck_research_review_claims_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"], ["public.research_checkpoints.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["public.users.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["public.users.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint("token", name="uq_research_review_claims_token"),
        schema="public",
    )
    _install_postgresql_revision_guard()


def downgrade() -> None:
    """仅回退 schema；删除的上下文封签不能恢复为历史数据。"""
    if context.is_offline_mode():
        raise RuntimeError("检查点上下文迁移需要在线升级以校验并封签旧数据")

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("检查点上下文迁移仅支持在线 PostgreSQL 升级")

    _drop_postgresql_revision_guard()
    op.drop_table("research_review_claims", schema="public")
    op.drop_constraint(
        "fk_research_checkpoint_integrities_checkpoint_session",
        "research_checkpoint_integrities", type_="foreignkey", schema="public",
    )
    op.drop_constraint(
        "ck_research_checkpoint_integrities_business_revision",
        "research_checkpoint_integrities", type_="check", schema="public",
    )
    op.drop_constraint(
        "uq_research_checkpoint_integrities_checkpoint_id",
        "research_checkpoint_integrities", type_="unique", schema="public",
    )
    op.execute("""
        ALTER TABLE public.research_checkpoint_integrities
        ALTER COLUMN checkpoint_id TYPE varchar(64) USING checkpoint_id::text
    """)
    op.drop_column("research_checkpoint_integrities", "context_seal", schema="public")
    op.alter_column("research_checkpoints", "status", nullable=True, schema="public")
    op.drop_constraint(
        "ck_research_checkpoints_status", "research_checkpoints", type_="check", schema="public",
    )
    op.drop_constraint(
        "uq_research_checkpoints_id_session_id", "research_checkpoints",
        type_="unique", schema="public",
    )
    op.drop_constraint(
        "uq_research_checkpoints_session_id", "research_checkpoints",
        type_="unique", schema="public",
    )
