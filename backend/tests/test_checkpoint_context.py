"""D4a pure seam coverage; database behavior is in the PostgreSQL suite."""
import asyncio
import copy
import uuid

import pytest

from test_checkpoint_integrity_persistence import _Db, _state
from test_review_workspace import _checkpoint, _state as _review_state, _DB, _REVIEWER
from test_graph_equivalence import _build_graph
from service.checkpoint_integrity import (
    CheckpointIntegrityError, CONTEXT_MIGRATION, MODE_STANDARD,
    issue_checkpoint_context_seal, verify_checkpoint_context_seal,
)
from service.checkpoint_service import CheckpointService
from service.review_workspace_service import ReviewWorkspaceIntegrityError, ReviewWorkspaceService


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for name in ("RESEARCH_CHECKPOINT_KEYS_JSON", "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID",
                 "RESEARCH_CHECKPOINT_LEGACY_KEY_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "context-test-secret")


@pytest.mark.parametrize("field,value", [
    ("checkpoint_id", uuid.uuid4()), ("session_id", "other"),
    ("owner_id", uuid.uuid4()), ("status", "completed"),
    ("revision", 2), ("business_seal", "b" * 64),
])
def test_context_binds_every_identity_and_basis_field(field, value):
    context = dict(checkpoint_id=uuid.uuid4(), session_id="context-session", owner_id=uuid.uuid4(),
                   status="paused", mode=MODE_STANDARD, revision=1, business_seal="a" * 64)
    seal = issue_checkpoint_context_seal(**context)
    verify_checkpoint_context_seal(**context, seal=seal)
    context[field] = value
    with pytest.raises(CheckpointIntegrityError):
        verify_checkpoint_context_seal(**context, seal=seal)


def test_context_origin_and_envelope_are_authenticated():
    context = dict(checkpoint_id=uuid.uuid4(), session_id="context-session", owner_id=None,
                   status="paused", mode=MODE_STANDARD, revision=1, business_seal="a" * 64)
    seal = issue_checkpoint_context_seal(**context, origin=CONTEXT_MIGRATION)
    for changes in ({"origin": "native_v1"}, {"version": True}, {"mac": "é" * 64},
                    {"extra": "ignored?"}, {"key_id": None}):
        with pytest.raises(CheckpointIntegrityError):
            verify_checkpoint_context_seal(**context, seal={**seal, **changes})


@pytest.mark.parametrize("mutate", [
    lambda cp, it: setattr(cp, "user_id", uuid.uuid4()),
    lambda cp, it: setattr(cp, "status", "paused"),
    lambda cp, it: setattr(it, "business_revision", 2),
    lambda cp, it: setattr(it, "context_seal", None),
])
def test_mutation_cannot_be_read_or_laundered_through_a_new_save(monkeypatch, mutate):
    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)
    state = _state()
    assert service.save_checkpoint("checkpoint-session", state)
    mutate(db.checkpoint, db.integrity)
    for read in (service.get_checkpoint_info, service.load_checkpoint, service.load_full_checkpoint):
        with pytest.raises(CheckpointIntegrityError):
            read("checkpoint-session")
    assert not service.save_checkpoint("checkpoint-session", state)
    assert not service.update_status("checkpoint-session", "completed")
    assert db.commits == 1


def test_progress_preserves_pause_and_status_transition_resigns(monkeypatch):
    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)
    assert service.save_checkpoint("checkpoint-session", _state())
    assert service.update_status("checkpoint-session", "paused")
    assert db.integrity.business_revision == 2
    old_seal = copy.deepcopy(db.integrity.context_seal)
    assert service.save_checkpoint("checkpoint-session", _state())
    assert db.checkpoint.status == "paused" and db.integrity.business_revision == 3
    assert db.integrity.context_seal != old_seal
    assert service.update_status("checkpoint-session", "completed")
    assert db.integrity.business_revision == 4
    assert service.update_status("checkpoint-session", "completed")  # No-op, no version churn.
    assert db.integrity.business_revision == 4
    assert not service.update_status("checkpoint-session", "running")
    assert not service.save_checkpoint("checkpoint-session", _state())


@pytest.mark.parametrize("owner", [None, _REVIEWER])
def test_forged_owner_cannot_hide_a_task_from_the_review_queue(owner):
    checkpoint, integrity = _checkpoint(_review_state())
    checkpoint.user_id = owner
    service = ReviewWorkspaceService(_DB([checkpoint], [integrity]))
    with pytest.raises(ReviewWorkspaceIntegrityError):
        service.list_pending(_REVIEWER)
    with pytest.raises(ReviewWorkspaceIntegrityError):
        service.get_pending(checkpoint.session_id, _REVIEWER)


@pytest.mark.parametrize("failure", ["save", "status"])
def test_graph_does_not_emit_completion_when_persistence_rejects(failure):
    graph = _build_graph()
    if failure == "save":
        graph.checkpoint_service.save_checkpoint = lambda **kwargs: None
    else:
        graph.checkpoint_service.update_status = lambda *args, **kwargs: False

    async def run():
        return [event async for event in graph.run("普通行业研究", "failed-persistence")]

    events = asyncio.run(run())
    assert any(event["type"] == "error" for event in events)
    assert not any(event["type"] == "research_complete" for event in events)
