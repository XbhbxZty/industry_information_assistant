"""Retain accepted review decisions and freeze finalized claim records.

Revision ID: 20260902_0004
Revises: 20260902_0003

No data rewrite or new tables. This guard protects ordinary database writes,
not a database administrator who can explicitly disable or drop the trigger.
"""
from alembic import context, op


revision = "20260902_0004"
down_revision = "20260902_0003"
branch_labels = None
depends_on = None

FUNCTION = "protect_accepted_research_review_decision"
TRIGGER = "research_review_claims_accepted_immutable"
TRUNCATE_TRIGGER = "research_review_claims_no_truncate"
FINALIZE_FUNCTION = "check_research_review_finalized_checkpoint"
FINALIZE_TRIGGER = "research_review_claims_finalized_checkpoint"


def _require_online_postgresql():
    if context.is_offline_mode():
        raise RuntimeError("复核决定保护迁移需要在线升级")
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("复核决定保护迁移仅支持 PostgreSQL")


def upgrade():
    _require_online_postgresql()
    op.execute("LOCK TABLE public.research_review_claims IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""
        CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'accepted review decision table cannot be truncated'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state IN ('accepted', 'finalized') THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'accepted review decision cannot be deleted'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.state = 'finalized' THEN
                    IF NEW IS DISTINCT FROM OLD THEN
                        RAISE EXCEPTION 'finalized review claim is immutable'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.state NOT IN ('accepted', 'finalized')
                   OR ROW(NEW.checkpoint_id, NEW.owner_id, NEW.reviewer_id, NEW.token,
                          NEW.basis_version, NEW.basis_seal, NEW.decision_digest,
                          NEW.decision_json, NEW.accepted_at, NEW.created_at)
                      IS DISTINCT FROM
                      ROW(OLD.checkpoint_id, OLD.owner_id, OLD.reviewer_id, OLD.token,
                          OLD.basis_version, OLD.basis_seal, OLD.decision_digest,
                          OLD.decision_json, OLD.accepted_at, OLD.created_at)
                   OR (NEW.state = 'finalized' AND NEW.finalized_at < OLD.accepted_at) THEN
                    RAISE EXCEPTION 'accepted review decision is immutable'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER {TRIGGER} BEFORE UPDATE OR DELETE
        ON public.research_review_claims
        FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}();
    """)
    op.execute(f"""
        CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE
        ON public.research_review_claims
        FOR EACH STATEMENT EXECUTE FUNCTION public.{FUNCTION}();
    """)
    # Check at commit, not in the BEFORE row trigger: ORM flush order between
    # integrity and claim rows is not an atomicity contract. No reverse-order
    # row locks are introduced; application writers already hold CP -> IT -> claim.
    op.execute(f"""
        CREATE FUNCTION public.{FINALIZE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.research_checkpoints AS cp
                JOIN public.research_checkpoint_integrities AS it
                  ON it.checkpoint_id = cp.id AND it.session_id = cp.session_id
                WHERE cp.id = NEW.checkpoint_id AND cp.user_id = NEW.owner_id
                  AND cp.status = 'completed' AND cp.phase = 'completed'
                  AND cp.state_json->>'phase' = 'completed'
                  AND it.business_revision = NEW.basis_version + 1
            ) THEN
                RAISE EXCEPTION 'finalized review requires its completed checkpoint'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""
        CREATE CONSTRAINT TRIGGER {FINALIZE_TRIGGER}
        AFTER UPDATE ON public.research_review_claims
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (OLD.state = 'accepted' AND NEW.state = 'finalized')
        EXECUTE FUNCTION public.{FINALIZE_FUNCTION}();
    """)


def downgrade():
    """Remove the guard only; accepted/finalized data remains unchanged."""
    _require_online_postgresql()
    op.execute(f"DROP TRIGGER {FINALIZE_TRIGGER} ON public.research_review_claims")
    op.execute(f"DROP FUNCTION public.{FINALIZE_FUNCTION}()")
    op.execute(f"DROP TRIGGER {TRUNCATE_TRIGGER} ON public.research_review_claims")
    op.execute(f"DROP TRIGGER {TRIGGER} ON public.research_review_claims")
    op.execute(f"DROP FUNCTION public.{FUNCTION}()")
