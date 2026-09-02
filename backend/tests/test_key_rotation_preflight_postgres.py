"""Real persistence and read-only preflight tests for D3, on disposable DBs only."""
from __future__ import annotations

import base64
import copy
import json
import uuid

from alembic import command
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from test_legacy_schema_preflight_postgres import (  # noqa: F401
    disposable_postgres_database, _alembic_config,
)
from test_admin_company_profiles import _write
from models.company_profile import AdminCompanyProfileAudit
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity
from service import admin_company_profile_service as profiles
from service.checkpoint_integrity import (
    CheckpointIntegrityError, GRAPH_SEAL_FIELD, MODE_MANAGED, MODE_STANDARD,
    issue_graph_state_seal, verify_graph_state_seal,
)
from service.checkpoint_service import CheckpointService
from service.deep_research_v2.state import create_initial_state, verify_admin_profile_snapshot_binding
from service.key_rotation_preflight import inspect_key_rotation


pytestmark = pytest.mark.postgres_integration
OLD_SECRET = "legacy-short-secret"


def _encode(value):
    return base64.b64encode(value).decode("ascii")


def _checkpoint_keys(monkeypatch, active="new-checkpoint", *, retain_old=True):
    keys = {"new-checkpoint": _encode(b"n" * 32), "unused-checkpoint": _encode(b"u" * 32)}
    if retain_old:
        keys["old-checkpoint"] = _encode(OLD_SECRET.encode("utf-8"))
        monkeypatch.setenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", "old-checkpoint")
    else:
        monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)
    monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", json.dumps(keys))
    monkeypatch.setenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", active)


def _audit_keys(monkeypatch, active="audit-old", *, retain_old=True):
    keys = {"audit-new": _encode(b"b" * 32), "audit-unused": _encode(b"c" * 32)}
    if retain_old:
        keys["audit-old"] = _encode(b"a" * 32)
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps(keys))
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", active)


@pytest.fixture
def rotation_db(disposable_postgres_database, monkeypatch):
    for name in ("RESEARCH_CHECKPOINT_KEYS_JSON", "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", "RESEARCH_CHECKPOINT_LEGACY_KEY_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", OLD_SECRET)
    _audit_keys(monkeypatch)
    command.upgrade(_alembic_config(disposable_postgres_database), "head")
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _checkpoint_service(engine):
    service = CheckpointService()
    service._get_db = lambda: Session(engine)
    return service


def _state(session_id="rotation-session", *, managed=False):
    if managed:
        ref = {"id": str(uuid.uuid4()), "revision": 1, "content_sha256": "a" * 64, "source": "admin_company_profile"}
        state = create_initial_state(
            "rotation test", session_id, due_diligence=True,
            provided_company_profile={"name": "rotation company"}, admin_profile_ref=ref,
            admin_profile_scenario="factoring",
        )
    else:
        state = {"session_id": session_id, "query": "rotation test", "phase": "planning", "iteration": 0}
    mode = MODE_MANAGED if managed else MODE_STANDARD
    state["_checkpoint_integrity_mode"] = mode
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, mode)
    return state


@pytest.mark.parametrize("managed", [False, True])
def test_old_checkpoint_restores_unchanged_after_rotation_and_new_writes_use_active(rotation_db, monkeypatch, managed):
    engine = rotation_db
    service = _checkpoint_service(engine)
    original = _state(managed=managed)
    assert service.save_checkpoint("rotation-session", original)
    with Session(engine) as db:
        before_state = copy.deepcopy(db.query(ResearchCheckpoint).one().state_json)
        seal = db.query(ResearchCheckpointIntegrity).one()
        before_metadata = (seal.key_id, seal.business_seal, seal.business_revision)
    _checkpoint_keys(monkeypatch)
    restored = service.load_checkpoint("rotation-session")
    assert restored is not None
    verify_graph_state_seal(restored, MODE_MANAGED if managed else MODE_STANDARD)
    if managed:
        assert restored["admin_profile_snapshot_binding"]["version"] == 2
        verify_admin_profile_snapshot_binding(
            restored["admin_profile_snapshot_binding"], "rotation-session",
            restored["provided_company_profile"], restored["admin_profile_ref"], "factoring",
        )
    with Session(engine) as db:
        assert db.query(ResearchCheckpoint).one().state_json == before_state
        seal = db.query(ResearchCheckpointIntegrity).one()
        assert (seal.key_id, seal.business_seal, seal.business_revision) == before_metadata
    restored["iteration"] = 1
    restored[GRAPH_SEAL_FIELD] = issue_graph_state_seal(restored, MODE_MANAGED if managed else MODE_STANDARD)
    assert service.save_checkpoint("rotation-session", restored)
    with Session(engine) as db:
        seal = db.query(ResearchCheckpointIntegrity).one()
        assert seal.key_id != before_metadata[0] and seal.business_revision == 2
    _checkpoint_keys(monkeypatch, active="old-checkpoint")
    assert service.load_checkpoint("rotation-session") is not None  # Active rollback keeps new verifier.
    _checkpoint_keys(monkeypatch, retain_old=False)
    if managed:
        # Outer seals are new, but the unchanged v2 snapshot still needs old key.
        assert not inspect_key_rotation(engine)["preflight_passed"]
    else:
        assert service.load_checkpoint("rotation-session") is not None


def test_inventory_counts_inner_snapshot_and_blocks_retirement_without_writes(rotation_db, monkeypatch):
    engine = rotation_db
    service = _checkpoint_service(engine)
    old = _state(managed=True)
    assert service.save_checkpoint("rotation-session", old)
    with Session(engine) as db:
        profile = profiles.create_company_profile(db, _write(), actor_id="admin", change_reason="create")
        profile_id = profile.id
        original_mac = db.query(AdminCompanyProfileAudit).one().audit_mac
    _audit_keys(monkeypatch, active="audit-new")
    _checkpoint_keys(monkeypatch)
    updated = service.load_checkpoint("rotation-session")
    updated["iteration"] = 1
    updated[GRAPH_SEAL_FIELD] = issue_graph_state_seal(updated, MODE_MANAGED)
    assert service.save_checkpoint("rotation-session", updated)
    with Session(engine) as db:
        profiles.update_company_profile(db, profile_id, _write(name="new version"), expected_revision=1, actor_id="admin", change_reason="update")

    from service import key_rotation_preflight as preflight_module
    real_collect = preflight_module._collect

    def assert_read_only(db, *args):
        assert db.execute(text("SHOW transaction_read_only")).scalar() == "on"
        assert db.execute(text("SHOW transaction_isolation")).scalar() == "repeatable read"
        assert db.execute(text("SHOW search_path")).scalar() == "public"
        return real_collect(db, *args)

    monkeypatch.setattr(preflight_module, "_collect", assert_read_only)
    report = inspect_key_rotation(engine, retire_audit_keys=["audit-old"], retire_checkpoint_keys=["old-checkpoint"])
    assert not report["preflight_passed"]
    assert report["references"]["audit"]["audit-old"] == {"audits": 1}
    assert report["references"]["checkpoint"]["old-checkpoint"] == {"snapshot_v2": 1}
    assert report["references"]["checkpoint"]["new-checkpoint"] == {"business": 1, "graph": 1, "context": 1}
    assert all("retained_records_reference_key" in item["reasons"] for item in report["retirement"]["blocked"])
    clean = inspect_key_rotation(engine, retire_audit_keys=["audit-unused"], retire_checkpoint_keys=["unused-checkpoint"])
    assert clean["preflight_passed"]
    assert OLD_SECRET not in json.dumps(report)
    assert "rotation company" not in json.dumps(report)
    with Session(engine) as db:
        first = db.query(AdminCompanyProfileAudit).filter_by(profile_id=profile_id, revision=1).one()
        assert first.audit_mac == original_mac
        assert db.query(ResearchCheckpointIntegrity).one().business_revision == 2


def test_opaque_langgraph_history_and_active_key_prevent_retirement(rotation_db, monkeypatch):
    _checkpoint_keys(monkeypatch)
    with rotation_db.begin() as connection:
        connection.execute(text("CREATE TABLE public.checkpoint_blobs (payload bytea)"))
        connection.execute(text("INSERT INTO public.checkpoint_blobs VALUES (decode('00', 'hex'))"))
    report = inspect_key_rotation(rotation_db, retire_checkpoint_keys=["unused-checkpoint", "new-checkpoint"])
    assert report["opaque_langgraph_history"] == ["checkpoint_blobs"]
    assert not report["preflight_passed"]
    blocked = {item["key_id"]: item["reasons"] for item in report["retirement"]["blocked"]}
    assert "active_signing_key" in blocked["new-checkpoint"]
    assert "opaque_langgraph_history_not_proven_key_free" in blocked["unused-checkpoint"]


def test_missing_historical_keys_and_wrong_audit_material_are_rejected(rotation_db, monkeypatch):
    service = _checkpoint_service(rotation_db)
    assert service.save_checkpoint("rotation-session", _state())
    with Session(rotation_db) as db:
        profiles.create_company_profile(db, _write(), actor_id="admin", change_reason="create")
    _checkpoint_keys(monkeypatch, retain_old=False)
    _audit_keys(monkeypatch, active="audit-new", retain_old=False)
    with pytest.raises(CheckpointIntegrityError):
        service.load_checkpoint("rotation-session")
    report = inspect_key_rotation(rotation_db)
    assert not report["preflight_passed"]
    assert report["unknown_key_references"]["audit"] == ["audit-old"]
    assert report["unknown_key_references"]["checkpoint"]
    _audit_keys(monkeypatch)
    _checkpoint_keys(monkeypatch)
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps({"audit-old": _encode(b"wrong-secret-must-fail-validation!")}))
    assert "audit_history_verification_failed" in inspect_key_rotation(rotation_db)["issues"]


def test_cli_is_read_only_and_outputs_counts_not_material(rotation_db, disposable_postgres_database, monkeypatch, capsys):
    _checkpoint_keys(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", disposable_postgres_database.sqlalchemy_url)
    from scripts.check_key_rotation import main
    assert main(["--retire-audit-key", "audit-unused"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"] is True and report["preflight_passed"] is True
    assert report["database"] == disposable_postgres_database.name
    assert main(["--retire-audit-key", "audit-old"]) == 2  # Current active key.
    assert "active_signing_key" in capsys.readouterr().out
    monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", "bad configuration should never be printed")
    assert main([]) == 2
    output = capsys.readouterr().out
    assert "bad configuration" not in output
    assert "checkpoint_key_configuration_invalid" in output
    from core import database_url

    def fail_resolver():
        raise ValueError("simulated-secret-that-must-not-be-printed")

    monkeypatch.setattr(database_url, "resolve_database_urls", fail_resolver)
    assert main([]) == 3
    captured = capsys.readouterr()
    assert "simulated-secret" not in captured.err
    assert json.loads(captured.err)["error_type"] == "ValueError"


def test_legacy_anchor_references_alone_block_audit_key_retirement(disposable_postgres_database, monkeypatch):
    from test_company_profile_audit_migration_postgres import _insert_legacy

    _audit_keys(monkeypatch)
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, "20260831_0001")
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            _insert_legacy(connection)
        command.upgrade(config, "head")
        _audit_keys(monkeypatch, active="audit-new")
        report = inspect_key_rotation(engine, retire_audit_keys=["audit-old"])
        assert not report["preflight_passed"]
        assert report["references"]["audit"]["audit-old"] == {"anchors": 1}
        assert report["legacy"]["unsigned_audits"] == 2
        assert report["retirement"]["blocked"][0]["reasons"] == ["retained_records_reference_key"]
    finally:
        engine.dispose()
