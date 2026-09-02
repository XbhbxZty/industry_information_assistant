"""D4a migration-time observations, never manufactured historical signatures.

The Alembic caller owns the transaction and locks. This helper never commits,
repairs missing integrity, changes v1 signatures, or constructs current ORM rows.
"""
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from service.checkpoint_integrity import (
    CONTEXT_MIGRATION, INTEGRITY_VERSION, CheckpointIntegrityError,
    issue_checkpoint_context_seal, verify_business_state_seal, verify_graph_state_seal,
)


def backfill_checkpoint_contexts(connection) -> None:
    rows = connection.execute(text("""
        SELECT c.id, c.session_id, c.user_id, c.status, c.state_json,
               i.checkpoint_id, i.mode, i.integrity_version, i.key_id,
               i.business_revision, i.business_seal, i.context_seal
        FROM public.research_checkpoints c
        JOIN public.research_checkpoint_integrities i ON i.session_id = c.session_id
        ORDER BY c.session_id
    """)).mappings()
    for row in rows:
        if str(row["checkpoint_id"]) != str(row["id"]):
            raise CheckpointIntegrityError("迁移拒绝错配的检查点")
        if row["context_seal"] is not None:
            raise CheckpointIntegrityError("迁移拒绝覆盖已有上下文封签")
        if type(row["integrity_version"]) is not int or row["integrity_version"] != INTEGRITY_VERSION:
            raise CheckpointIntegrityError("迁移不支持该检查点版本")
        if not isinstance(row["key_id"], str) or not row["key_id"]:
            raise CheckpointIntegrityError("迁移拒绝缺失的检查点密钥标识")
        state = row["state_json"]
        verify_graph_state_seal(state, row["mode"])
        verify_business_state_seal(
            state, row["session_id"], row["mode"], row["business_revision"],
            row["business_seal"], key_id=row["key_id"],
        )
        if state.get("session_id") != row["session_id"]:
            raise CheckpointIntegrityError("迁移拒绝状态 session_id 错配")
        seal = issue_checkpoint_context_seal(
            row["id"], row["session_id"], row["user_id"], row["status"],
            row["mode"], row["business_revision"], row["business_seal"],
            origin=CONTEXT_MIGRATION,
        )
        connection.execute(text("""
            UPDATE public.research_checkpoint_integrities
            SET context_seal = :seal WHERE session_id = :session_id
        """).bindparams(bindparam("seal", type_=JSONB)),
            {"seal": seal, "session_id": row["session_id"]})
