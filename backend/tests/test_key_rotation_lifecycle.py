"""Service-level audit rotation and real LangGraph interrupt/resume coverage."""
from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest

from test_admin_company_profiles import db, _write  # noqa: F401
from test_admin_profile_resume_integrity import _snapshot, _collect, QUERY
from service import admin_company_profile_service as profiles
from service.checkpoint_integrity import GRAPH_SEAL_FIELD, MODE_MANAGED, verify_graph_state_seal


def test_actual_profile_service_crosses_rotation_and_active_rollback_without_resigning(db, monkeypatch):
    row = profiles.create_company_profile(db, _write(), actor_id="admin", change_reason="create")
    first = profiles.list_company_profile_history(db, row.id)[0]
    original_mac, original_id = first.audit_mac, first.key_id
    keys = json.loads(os.environ["COMPANY_PROFILE_AUDIT_KEYS_JSON"])
    keys["new-key"] = base64.b64encode(b"new-audit-key-material-for-rotation").decode("ascii")
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps(keys))
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", "new-key")
    profiles.update_company_profile(db, row.id, _write(name="rotated"), expected_revision=1, actor_id="admin", change_reason="update")
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", original_id)
    profiles.archive_company_profile(db, row.id, expected_revision=2, actor_id="admin", change_reason="archive")
    audits = profiles.validate_company_profile_audit_history(db, row.id)
    assert [a.key_id for a in audits] == [original_id, "new-key", original_id]
    assert audits[0].audit_mac == original_mac
    assert audits[1].previous_audit_mac == original_mac
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps({"new-key": keys["new-key"]}))
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", "new-key")
    with pytest.raises(profiles.AdminCompanyProfileUnavailable):
        profiles.get_company_profile(db, row.id, include_archived=True)


def test_real_langgraph_review_resumes_across_key_rotation(monkeypatch):
    from test_graph_equivalence import _build_graph

    old = "old-managed-langgraph-secret"
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", old)
    for name in ("RESEARCH_CHECKPOINT_KEYS_JSON", "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", "RESEARCH_CHECKPOINT_LEGACY_KEY_ID"):
        monkeypatch.delenv(name, raising=False)
    profile, ref, scenario = _snapshot()
    graph = _build_graph(requires_review=True)
    session_id = "rotation-real-langgraph-review"
    first = asyncio.run(_collect(graph.run(
        QUERY, session_id, user_id="u1", due_diligence=True,
        provided_company_profile=profile, admin_profile_ref=ref, admin_profile_scenario=scenario,
    )))
    assert first[-1]["type"] == "human_review_required"
    config = {"configurable": {"thread_id": session_id}}
    paused = asyncio.run(graph.graph.aget_state(config))
    old_seal = dict(paused.values[GRAPH_SEAL_FIELD])
    assert paused.values["admin_profile_snapshot_binding"]["version"] == 2
    monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", json.dumps({
        "old": base64.b64encode(old.encode("utf-8")).decode("ascii"),
        "new": base64.b64encode(b"n" * 32).decode("ascii"),
    }))
    monkeypatch.setenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", "new")
    monkeypatch.setenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", "old")
    verify_graph_state_seal(dict(paused.values), MODE_MANAGED)
    second = asyncio.run(_collect(graph.resume_review(
        session_id, {"approved": True, "reviewer": "reviewer", "comment": "rotated"}, user_id="u1",
    )))
    assert second[-1]["type"] == "research_complete"
    completed = asyncio.run(graph.graph.aget_state(config))
    verify_graph_state_seal(dict(completed.values), MODE_MANAGED)
    assert completed.values[GRAPH_SEAL_FIELD]["key_id"] != old_seal["key_id"]
    assert completed.values["admin_profile_snapshot_binding"] == paused.values["admin_profile_snapshot_binding"]


def test_persistence_never_treats_missing_stored_key_id_as_use_active(monkeypatch):
    from test_checkpoint_integrity_persistence import _Db, _state
    from service.checkpoint_integrity import CheckpointIntegrityError
    from service.checkpoint_service import CheckpointService

    db = _Db()
    service = CheckpointService()
    monkeypatch.setattr(service, "_get_db", lambda: db)
    assert service.save_checkpoint("checkpoint-session", _state())
    db.integrity.key_id = None
    with pytest.raises(CheckpointIntegrityError):
        service.load_checkpoint("checkpoint-session")


def test_snapshot_non_ascii_mac_is_a_validation_error_not_compare_digest_type_error(monkeypatch):
    from service.deep_research_v2.state import create_admin_profile_snapshot_binding, verify_admin_profile_snapshot_binding

    monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", json.dumps({"new": base64.b64encode(b"n" * 32).decode("ascii")}))
    monkeypatch.setenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", "new")
    monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)
    binding = create_admin_profile_snapshot_binding("s", {"name": "new"}, {"id": 1}, "")
    binding["mac"] = "１" * 64
    with pytest.raises(ValueError, match="MAC"):
        verify_admin_profile_snapshot_binding(binding, "s", {"name": "new"}, {"id": 1}, "")
