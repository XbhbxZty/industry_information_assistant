"""Focused tests for the checkpoint integrity primitives and persistence seam.

The production checkpoint column is PostgreSQL JSONB, so this module uses a
small in-memory session double for service behavior and leaves database type
coverage to integration deployment.  Most assertions exercise the pure HMAC
format directly.
"""
from __future__ import annotations

import copy
import os
import sys
import uuid
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity  # noqa: E402
from service.checkpoint_integrity import (  # noqa: E402
    CheckpointIntegrityError,
    GRAPH_SEAL_FIELD,
    MODE_MANAGED,
    MODE_STANDARD,
    issue_business_state_seal,
    issue_graph_state_seal,
    mode_from_state,
    verify_business_state_seal,
    verify_graph_state_seal,
)
from service.checkpoint_service import CheckpointService  # noqa: E402


@pytest.fixture(autouse=True)
def _checkpoint_hmac_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "checkpoint-integrity-test-key")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)


def _state(mode: str = MODE_STANDARD) -> dict:
    state = {
        "session_id": "checkpoint-session",
        "query": "测试企业",
        "phase": "planning",
        "iteration": 0,
        "company_profile": {
            "name": "测试企业有限公司",
            "_admin_profile_snapshot_authorized": True,
            "_admin_profile_ref": {"id": "audit-ref"},
        },
        "business_value": {"risk": "medium"},
        "_message_queue": object(),
        "_checkpoint_integrity_mode": mode,
    }
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, mode)
    return state


def test_graph_seal_rejects_business_mutation_and_strict_shape():
    state = _state(MODE_MANAGED)
    verify_graph_state_seal(state, MODE_MANAGED)

    state["business_value"]["risk"] = "high"
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_MANAGED)

    state = _state()
    state[GRAPH_SEAL_FIELD]["extra"] = "not permitted"
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_STANDARD)


def test_graph_projection_excludes_only_runtime_private_fields_and_marker():
    state = _state()
    # Neither private runtime data nor the restore-only nested marker changes
    # the business projection.  Other nested underscore fields stay covered.
    state["_message_queue"] = object()
    state["company_profile"]["_admin_profile_snapshot_authorized"] = False
    verify_graph_state_seal(state, MODE_STANDARD)

    state["company_profile"]["_admin_profile_ref"]["id"] = "tampered"
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_STANDARD)


def test_business_seal_binds_session_mode_and_revision():
    state = _state()
    seal = issue_business_state_seal(state, "checkpoint-session", MODE_STANDARD, 1)
    verify_business_state_seal(state, "checkpoint-session", MODE_STANDARD, 1, seal)

    for session_id, mode, revision in (
        ("other-session", MODE_STANDARD, 1),
        ("checkpoint-session", MODE_MANAGED, 1),
        ("checkpoint-session", MODE_STANDARD, 2),
    ):
        with pytest.raises(CheckpointIntegrityError):
            verify_business_state_seal(state, session_id, mode, revision, seal)


def test_missing_server_key_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY")
    with pytest.raises(CheckpointIntegrityError, match="JWT_SECRET_KEY"):
        issue_graph_state_seal({"query": "x"}, MODE_STANDARD)


def test_jwt_secret_is_a_supported_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY")
    monkeypatch.setenv("JWT_SECRET_KEY", "fallback-server-secret")
    state = {"query": "fallback"}
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    verify_graph_state_seal(state, MODE_STANDARD)


def test_empty_graph_seal_is_only_an_unsigned_construction_placeholder():
    state = {"_checkpoint_integrity_mode": MODE_STANDARD, GRAPH_SEAL_FIELD: {}}
    assert mode_from_state(state) == MODE_STANDARD
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_STANDARD)


class _Query:
    def __init__(self, db: "_Db", model):
        self.db = db
        self.model = model

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        if self.model is ResearchCheckpoint:
            return self.db.checkpoint
        if self.model is ResearchCheckpointIntegrity:
            return self.db.integrity
        raise AssertionError(f"unexpected model {self.model}")

    def delete(self):
        if self.model is ResearchCheckpoint:
            present = self.db.checkpoint is not None
            self.db.checkpoint = None
            return int(present)
        if self.model is ResearchCheckpointIntegrity:
            present = self.db.integrity is not None
            self.db.integrity = None
            return int(present)
        raise AssertionError(f"unexpected model {self.model}")


class _Db:
    def __init__(self):
        self.checkpoint = None
        self.integrity = None
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def query(self, model):
        return _Query(self, model)

    def add(self, row):
        if isinstance(row, ResearchCheckpoint):
            self.checkpoint = row
        elif isinstance(row, ResearchCheckpointIntegrity):
            self.integrity = row
        else:  # pragma: no cover - makes unexpected persistence explicit
            raise AssertionError(type(row))

    def flush(self):
        if self.checkpoint is not None and self.checkpoint.id is None:
            self.checkpoint.id = uuid.uuid4()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_service_pairs_rows_revisions_and_excludes_ui_from_business_seal(monkeypatch: pytest.MonkeyPatch):
    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)

    state = _state()
    checkpoint_id = service.save_checkpoint(
        "checkpoint-session", state, ui_state={"progress": 10}
    )
    assert checkpoint_id == str(db.checkpoint.id)
    assert db.integrity.checkpoint_id == checkpoint_id
    assert db.integrity.mode == MODE_STANDARD
    assert db.integrity.business_revision == 1
    assert "_checkpoint_integrity_mode" not in db.checkpoint.state_json
    assert db.checkpoint.ui_state_json == {"progress": 10}

    # UI is separate from the business state; changing it must not invalidate
    # the paired business seal or leak a private mode in the API response.
    db.checkpoint.ui_state_json = {"progress": "tampered-ui"}
    full = service.load_full_checkpoint("checkpoint-session")
    assert "_checkpoint_integrity_mode" not in full["state_json"]

    next_state = copy.deepcopy(state)
    next_state["phase"] = "researching"
    next_state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(next_state, MODE_STANDARD)
    assert service.save_checkpoint("checkpoint-session", next_state) == checkpoint_id
    assert db.integrity.business_revision == 2
    assert db.integrity.checkpoint_id == checkpoint_id
    assert db.integrity.mode == MODE_STANDARD
    assert db.commits == 2

    restored = service.load_checkpoint("checkpoint-session")
    assert restored["_checkpoint_integrity_mode"] == MODE_STANDARD


def test_service_fails_closed_for_paired_row_mutation(monkeypatch: pytest.MonkeyPatch):
    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)
    assert service.save_checkpoint("checkpoint-session", _state())

    db.checkpoint.state_json["query"] = "tampered persisted business state"
    with pytest.raises(CheckpointIntegrityError):
        service.load_checkpoint("checkpoint-session")
    with pytest.raises(CheckpointIntegrityError):
        service.get_checkpoint_integrity_mode("checkpoint-session")


def test_service_rejects_cleaner_induced_public_state_change(monkeypatch: pytest.MonkeyPatch):
    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)
    original_cleaner = service._clean_state_for_storage

    def _tampering_cleaner(value):
        cleaned = original_cleaner(value)
        if isinstance(cleaned, dict) and "query" in cleaned:
            cleaned["query"] = "cleaner changed signed public state"
        return cleaned

    monkeypatch.setattr(service, "_clean_state_for_storage", _tampering_cleaner)

    assert service.save_checkpoint("checkpoint-session", _state()) is None
    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.integrity is None


def test_service_keeps_legacy_row_legacy(monkeypatch: pytest.MonkeyPatch):
    db = _Db()
    db.checkpoint = ResearchCheckpoint(
        id=uuid.uuid4(), session_id="legacy", query="legacy", phase="planning",
        iteration=0, state_json={"query": "legacy"}, status="running",
    )
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)

    assert service.load_checkpoint("legacy") == {"query": "legacy"}
    assert service.get_checkpoint_integrity_mode("legacy") is None
