"""D4a real PostgreSQL migration and persistence tests, disposable databases only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import uuid

from alembic import command
import pytest
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_legacy_schema_preflight_postgres import disposable_postgres_database, _alembic_config  # noqa: F401
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity, ResearchReviewClaim
from models.user import User
from service.checkpoint_integrity import (
    CheckpointIntegrityError, CONTEXT_MIGRATION, GRAPH_SEAL_FIELD, MODE_STANDARD,
    business_key_id, issue_business_state_seal, issue_graph_state_seal,
)
from service.checkpoint_service import CheckpointService
from service import checkpoint_context_backfill


pytestmark = pytest.mark.postgres_integration


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for name in ("RESEARCH_CHECKPOINT_KEYS_JSON", "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID",
                 "RESEARCH_CHECKPOINT_LEGACY_KEY_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "context-pg-test-secret")


def _state(session_id):
    state = {"query": "context test", "session_id": session_id, "phase": "reviewing",
             "iteration": 1, "risk_assessment": {"requires_human_review": True}}
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    return state


def _service(engine):
    service = CheckpointService()
    service._get_db = lambda: Session(engine)
    return service


def _legacy_row(connection, session_id, *, integrity=True):
    state, checkpoint_id = _state(session_id), uuid.uuid4()
    connection.execute(text("""
        INSERT INTO public.research_checkpoints
            (id, session_id, query, phase, state_json, status)
        VALUES (:id, :session_id, 'context test', 'reviewing', :state, 'paused')
    """).bindparams(bindparam("state", type_=JSONB)),
        {"id": checkpoint_id, "session_id": session_id, "state": state})
    seal = issue_business_state_seal(state, session_id, MODE_STANDARD, 7)
    if integrity:
        connection.execute(text("""
            INSERT INTO public.research_checkpoint_integrities
                (session_id, checkpoint_id, mode, integrity_version, key_id, business_revision, business_seal)
            VALUES (:session, :id, :mode, 1, :key, 7, :seal)
        """), {"session": session_id, "id": str(checkpoint_id), "mode": MODE_STANDARD,
               "key": business_key_id(MODE_STANDARD), "seal": seal})
    return checkpoint_id, state, seal


@pytest.fixture
def context_db(disposable_postgres_database):
    command.upgrade(_alembic_config(disposable_postgres_database), "head")
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_migration_observes_legacy_context_without_rewriting_v1(disposable_postgres_database):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, "20260902_0002")
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            cp_id, state, seal = _legacy_row(connection, "legacy")
        command.upgrade(config, "head")
        command.check(config)
        with Session(engine) as db:
            checkpoint = db.query(ResearchCheckpoint).one()
            integrity = db.query(ResearchCheckpointIntegrity).one()
            assert checkpoint.id == cp_id and checkpoint.state_json == state
            assert integrity.business_revision == 7 and integrity.business_seal == seal
            assert integrity.context_seal["origin"] == CONTEXT_MIGRATION
        assert _service(engine).get_checkpoint_info("legacy")["status"] == "paused"
        assert _service(engine).save_checkpoint("legacy", state)
        with Session(engine) as db:
            integrity = db.query(ResearchCheckpointIntegrity).one()
            assert integrity.business_revision == 8
            assert integrity.context_seal["origin"] == "native_v1"
    finally:
        engine.dispose()


@pytest.mark.parametrize("damage", [
    "duplicate_session", "invalid_status", "null_status", "missing_integrity",
    "orphan_integrity", "invalid_uuid", "invalid_revision", "bad_graph", "missing_key",
])
def test_migration_rejects_ambiguous_or_damaged_data_and_rolls_back(
    disposable_postgres_database, monkeypatch, damage,
):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, "20260902_0002")
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            _legacy_row(connection, "a-valid")
            _legacy_row(connection, "z-target", integrity=damage != "missing_integrity")
            commands = {
                "invalid_status": "UPDATE research_checkpoints SET status='arbitrary' WHERE session_id='z-target'",
                "null_status": "UPDATE research_checkpoints SET status=NULL WHERE session_id='z-target'",
                "orphan_integrity": "UPDATE research_checkpoint_integrities SET session_id='orphan' WHERE session_id='z-target'",
                "invalid_uuid": "UPDATE research_checkpoint_integrities SET checkpoint_id='invalid' WHERE session_id='z-target'",
                "invalid_revision": "UPDATE research_checkpoint_integrities SET business_revision=0 WHERE session_id='z-target'",
                "bad_graph": "UPDATE research_checkpoints SET state_json=jsonb_set(state_json, '{query}', '\"forged\"') WHERE session_id='z-target'",
            }
            if damage in commands:
                connection.execute(text(commands[damage]))
            elif damage == "duplicate_session":
                _legacy_row(connection, "z-target", integrity=False)
        if damage == "missing_key":
            monkeypatch.delenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", raising=False)
            monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        observed = []
        original = checkpoint_context_backfill.issue_checkpoint_context_seal

        def record(*args, **kwargs):
            seal = original(*args, **kwargs)
            observed.append(args[1])
            return seal

        monkeypatch.setattr(checkpoint_context_backfill, "issue_checkpoint_context_seal", record)
        with pytest.raises((RuntimeError, CheckpointIntegrityError)):
            command.upgrade(config, "head")
        if damage == "bad_graph":
            assert observed == ["a-valid"]  # A prior UPDATE and all DDL must roll back.
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260902_0002"
            assert connection.execute(text("SELECT count(*) FROM research_checkpoints")).scalar() == (3 if damage == "duplicate_session" else 2)
            assert connection.execute(text("SELECT business_revision FROM research_checkpoint_integrities WHERE session_id='a-valid'")).scalar() == 7
        assert "context_seal" not in {col["name"] for col in inspect(engine).get_columns("research_checkpoint_integrities")}
        assert "research_review_claims" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def _users(engine):
    ids = [uuid.uuid4(), uuid.uuid4()]
    with Session(engine) as db:
        for index, user_id in enumerate(ids):
            db.add(User(id=user_id, username=f"context-{index}", email=f"context-{index}@test.invalid", hashed_password="unused"))
        db.commit()
    return ids


def test_real_persistence_binds_owner_status_and_monotonic_basis(context_db):
    engine = context_db
    owner, other = _users(engine)
    service = _service(engine)
    assert service.save_checkpoint("context", _state("context"), user_id=str(owner))
    assert not service.save_checkpoint("context", _state("context"), user_id=str(other))
    assert service.update_status("context", "paused")
    assert service.save_checkpoint("context", _state("context"))
    with Session(engine) as db:
        assert db.query(ResearchCheckpointIntegrity).one().business_revision == 3
    assert service.get_checkpoint_info("context")["status"] == "paused"
    assert service.update_status("context", "completed")
    assert not service.update_status("context", "running")
    assert not service.save_checkpoint("context", _state("context"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE research_checkpoints SET user_id=:owner"), {"owner": other})
    with pytest.raises(CheckpointIntegrityError):
        service.get_checkpoint_info("context")
    assert not service.update_status("context", "completed")


@pytest.mark.parametrize("damage", ["status", "revision", "context", "missing_integrity"])
def test_real_corruption_never_passes_read_or_save(context_db, damage):
    service = _service(context_db)
    assert service.save_checkpoint("context", _state("context"))
    statements = {
        "status": "UPDATE research_checkpoints SET status='paused'",
        "revision": "UPDATE research_checkpoint_integrities SET business_revision=business_revision+1",
        "context": "UPDATE research_checkpoint_integrities SET business_revision=business_revision+1, context_seal='{}'::jsonb",
        "missing_integrity": "DELETE FROM research_checkpoint_integrities",
    }
    with context_db.begin() as connection:
        connection.execute(text(statements[damage]))
    for read in (service.get_checkpoint_info, service.load_checkpoint, service.load_full_checkpoint):
        with pytest.raises(CheckpointIntegrityError):
            read("context")
    assert not service.save_checkpoint("context", _state("context"))


def test_independent_writers_serialize_business_version(context_db):
    service = _service(context_db)
    assert service.save_checkpoint("concurrent", _state("concurrent"))
    gate = Barrier(2)

    def save():
        gate.wait(timeout=10)
        return _service(context_db).save_checkpoint("concurrent", _state("concurrent"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save), pool.submit(save)]
        assert all(future.result(timeout=15) for future in futures)
    with Session(context_db) as db:
        assert db.query(ResearchCheckpointIntegrity).one().business_revision == 3
    assert service.load_checkpoint("concurrent")


def test_real_graph_pause_and_resume_with_postgres_context(context_db):
    from test_graph_equivalence import _build_graph
    from test_admin_profile_resume_integrity import _snapshot, _collect, QUERY
    from service.review_workspace_service import ReviewWorkspaceService

    owner, reviewer = _users(context_db)
    graph = _build_graph(requires_review=True)
    graph.checkpoint_service = _service(context_db)
    profile, ref, scenario = _snapshot()
    session_id = "context-graph-review"
    first = asyncio.run(_collect(graph.run(
        QUERY, session_id, user_id=str(owner), due_diligence=True,
        provided_company_profile=profile, admin_profile_ref=ref, admin_profile_scenario=scenario,
    )))
    assert first[-1]["type"] == "human_review_required"
    with Session(context_db) as db:
        assert ReviewWorkspaceService(db).authorize_submission(session_id, reviewer) == str(owner)
        assert db.query(ResearchCheckpoint).one().status == "paused"
        before = db.query(ResearchCheckpointIntegrity).one().business_revision
    second = asyncio.run(_collect(graph.resume_review(
        session_id, {"approved": True, "reviewer": "reviewer", "reviewer_id": str(reviewer),
                     "comment": "context integration"}, user_id=str(owner),
    )))
    assert second[-1]["type"] == "research_complete"
    restored = graph.checkpoint_service.load_checkpoint(session_id)
    assert restored["risk_assessment"]["human_review"]["reviewer_id"] == str(reviewer)
    with Session(context_db) as db:
        assert db.query(ResearchCheckpoint).one().user_id == owner
        assert db.query(ResearchCheckpoint).one().status == "completed"
        assert db.query(ResearchCheckpointIntegrity).one().business_revision > before


def test_database_constraints_and_claim_skeleton(context_db):
    owner, reviewer = _users(context_db)
    service = _service(context_db)
    cp_id = uuid.UUID(service.save_checkpoint("context", _state("context"), user_id=str(owner)))
    with context_db.begin() as connection:
        for statement in (
            "UPDATE research_checkpoints SET status=NULL",
            "UPDATE research_checkpoints SET status='illegal'",
            "UPDATE research_checkpoint_integrities SET business_revision=business_revision",
            "UPDATE research_checkpoint_integrities SET business_revision=0",
            "UPDATE research_checkpoint_integrities SET business_revision=business_revision+1, context_seal=NULL",
            "UPDATE research_checkpoint_integrities SET business_revision=business_revision+1, session_id='wrong'",
        ):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(statement))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                _legacy_row(connection, "context", integrity=False)
    now = datetime.now(timezone.utc)
    fields = dict(checkpoint_id=cp_id, owner_id=owner, reviewer_id=reviewer, token=uuid.uuid4().hex,
                  basis_version=1, basis_seal="a" * 64, state="claimed", decision_json=None,
                  lease_expires_at=now + timedelta(minutes=5))
    with Session(context_db) as db:
        for changes in ({"checkpoint_id": uuid.uuid4()}, {"reviewer_id": uuid.uuid4()},
                        {"reviewer_id": owner}, {"basis_version": 0}, {"state": "unknown"},
                        {"state": "finalized"}, {"state": "accepted"}):
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.add(ResearchReviewClaim(**{**fields, **changes}))
                    db.flush()
        db.add(ResearchReviewClaim(**fields))
        db.commit()
    assert not service.delete_checkpoint("context")  # Claim retains the decision target.
    with Session(context_db) as db:
        with pytest.raises(IntegrityError):
            db.add(ResearchReviewClaim(**{**fields, "token": uuid.uuid4().hex}))
            db.commit()
        db.rollback()
        assert db.query(ResearchCheckpoint).count() == 1
        assert db.query(ResearchCheckpointIntegrity).count() == 1
