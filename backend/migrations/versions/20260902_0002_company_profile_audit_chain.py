"""Add sealed company-profile audit chains and legacy-history anchors.

Revision ID: 20260902_0002
Revises: 20260831_0001

Existing audit rows intentionally remain unsigned.  The migration backfill
creates one signed anchor per legacy profile, rather than manufacturing MACs
for historical records that were never signed at write time.
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260902_0002"
down_revision: Union[str, None] = "20260831_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUDIT_MUTATION_FUNCTION = "reject_admin_company_profile_audit_mutation"
_ANCHOR_MUTATION_FUNCTION = "reject_admin_company_profile_audit_anchor_mutation"
_AUDIT_ROW_TRIGGER = "admin_company_profile_audits_reject_row_mutation"
_AUDIT_TRUNCATE_TRIGGER = "admin_company_profile_audits_reject_truncate"
_ANCHOR_ROW_TRIGGER = "admin_company_profile_audit_anchors_reject_row_mutation"
_ANCHOR_TRUNCATE_TRIGGER = "admin_company_profile_audit_anchors_reject_truncate"


def _install_postgresql_immutability_guards() -> None:
    """Reject direct history rewriting while retaining insert-only behavior."""
    for function_name, table_name in (
        (_AUDIT_MUTATION_FUNCTION, "admin_company_profile_audits"),
        (_ANCHOR_MUTATION_FUNCTION, "admin_company_profile_audit_anchors"),
    ):
        op.execute(
            f"""
            CREATE FUNCTION public.{function_name}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'company profile audit history is append-only'
                    USING ERRCODE = '55000';
            END;
            $$;
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_reject_row_mutation
            BEFORE UPDATE OR DELETE ON public.{table_name}
            FOR EACH ROW EXECUTE FUNCTION public.{function_name}();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_reject_truncate
            BEFORE TRUNCATE ON public.{table_name}
            FOR EACH STATEMENT EXECUTE FUNCTION public.{function_name}();
            """
        )


def _drop_postgresql_immutability_guards() -> None:
    for trigger_name, table_name in (
        (_AUDIT_TRUNCATE_TRIGGER, "admin_company_profile_audits"),
        (_AUDIT_ROW_TRIGGER, "admin_company_profile_audits"),
        (_ANCHOR_TRUNCATE_TRIGGER, "admin_company_profile_audit_anchors"),
        (_ANCHOR_ROW_TRIGGER, "admin_company_profile_audit_anchors"),
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name};"
        )
    for function_name in (_AUDIT_MUTATION_FUNCTION, _ANCHOR_MUTATION_FUNCTION):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function_name}();")


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("企业档案审计锚定需要在线升级以校验旧历史")
    bind = op.get_bind()
    # Alembic's managed production schema is ``public``.  Keep the SQLite
    # migration path schema-less, while never relying on a caller's PostgreSQL
    # search_path for either DDL or the ORM backfill query.
    schema = "public" if bind.dialect.name == "postgresql" else None
    op.add_column(
        "admin_company_profiles",
        sa.Column("audit_head_mac", sa.String(length=64), nullable=True),
        schema=schema,
    )
    op.add_column(
        "admin_company_profiles",
        sa.Column("audit_head_snapshot_sha256", sa.String(length=64), nullable=True),
        schema=schema,
    )

    for name, type_ in (
        ("integrity_version", sa.Integer()),
        ("integrity_algorithm", sa.String(length=32)),
        ("key_id", sa.String(length=128)),
        ("before_snapshot_sha256", sa.String(length=64)),
        ("after_snapshot_sha256", sa.String(length=64)),
        ("previous_audit_mac", sa.String(length=64)),
        ("audit_mac", sa.String(length=64)),
        ("chain_start", sa.String(length=16)),
    ):
        op.add_column(
            "admin_company_profile_audits",
            sa.Column(name, type_, nullable=True),
            schema=schema,
        )

    op.create_table(
        "admin_company_profile_audit_anchors",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("integrity_version", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("key_id", sa.String(length=128), nullable=False),
        sa.Column("legacy_cutover_revision", sa.Integer(), nullable=False),
        sa.Column("legacy_chain_sha256", sa.String(length=64), nullable=False),
        sa.Column("legacy_terminal_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchored_at", sa.DateTime(), nullable=False),
        sa.Column("migration_run_id", sa.String(length=128), nullable=False),
        sa.Column("anchor_mac", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["public.admin_company_profiles.id"]
            if schema == "public" else ["admin_company_profiles.id"],
        ),
        sa.PrimaryKeyConstraint("profile_id"),
        schema=schema,
    )

    # The helper runs in Alembic's caller-owned transaction, flushes only, and
    # creates no records for an empty database.  It owns the signed payload
    # construction and therefore must not be replaced by migration SQL.
    from service.company_profile_audit_backfill import backfill_company_profile_audit_anchors

    if schema == "public":
        op.execute("SELECT set_config('search_path', 'public', true)")
    backfill_company_profile_audit_anchors(bind, migration_run_id=revision)

    if bind.dialect.name == "postgresql":
        _install_postgresql_immutability_guards()


def downgrade() -> None:
    bind = op.get_bind()
    schema = "public" if bind.dialect.name == "postgresql" else None
    if schema == "public":
        _drop_postgresql_immutability_guards()

    op.drop_table("admin_company_profile_audit_anchors", schema=schema)

    for name in (
        "chain_start",
        "audit_mac",
        "previous_audit_mac",
        "after_snapshot_sha256",
        "before_snapshot_sha256",
        "key_id",
        "integrity_algorithm",
        "integrity_version",
    ):
        op.drop_column("admin_company_profile_audits", name, schema=schema)
    op.drop_column(
        "admin_company_profiles", "audit_head_snapshot_sha256", schema=schema,
    )
    op.drop_column("admin_company_profiles", "audit_head_mac", schema=schema)
