"""Regression coverage for checkpoint key rotation and snapshot binding v3."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.checkpoint_keys import (  # noqa: E402
    CheckpointKeyConfigurationError,
    load_checkpoint_keyring,
)
from service.checkpoint_integrity import (  # noqa: E402
    CheckpointIntegrityError,
    GRAPH_SEAL_FIELD,
    MODE_STANDARD,
    business_key_id,
    issue_business_state_seal,
    issue_graph_state_seal,
    verify_business_state_seal,
    verify_graph_state_seal,
)
from service.deep_research_v2.state import (  # noqa: E402
    create_admin_profile_snapshot_binding,
    verify_admin_profile_snapshot_binding,
)


def _set_ring(monkeypatch: pytest.MonkeyPatch, keys: dict[str, bytes], active: str, legacy: str | None):
    monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", json.dumps({
        alias: base64.b64encode(value).decode("ascii") for alias, value in keys.items()
    }))
    monkeypatch.setenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", active)
    if legacy is None:
        monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)
    else:
        monkeypatch.setenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", legacy)


def _state() -> dict:
    return {"session_id": "rotation-session", "query": "rotate", "value": {"n": 1}}


def test_explicit_ring_restores_pre_rotation_v1_checkpoint_and_v2_snapshot(monkeypatch: pytest.MonkeyPatch):
    old = b"historical server secret accepted before rotation"
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", old.decode("ascii"))
    monkeypatch.delenv("RESEARCH_CHECKPOINT_KEYS_JSON", raising=False)
    monkeypatch.delenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", raising=False)
    monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)

    state = _state()
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    business = issue_business_state_seal(state, "rotation-session", MODE_STANDARD, 1)
    legacy_business_id = business_key_id(MODE_STANDARD)
    binding = create_admin_profile_snapshot_binding("rotation-session", {"name": "old"}, {"id": 1}, "s")

    _set_ring(monkeypatch, {"old": old, "new": b"n" * 32}, "new", "old")
    verify_graph_state_seal(state, MODE_STANDARD)
    verify_business_state_seal(
        state, "rotation-session", MODE_STANDARD, 1, business, key_id=legacy_business_id,
    )
    verify_admin_profile_snapshot_binding(binding, "rotation-session", {"name": "old"}, {"id": 1}, "s")


def test_new_writes_use_active_key_and_active_can_be_rolled_back(monkeypatch: pytest.MonkeyPatch):
    old, new = b"o" * 32, b"n" * 32
    _set_ring(monkeypatch, {"old": old, "new": new}, "new", "old")
    state = _state()
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    new_business_id = business_key_id(MODE_STANDARD)
    assert state[GRAPH_SEAL_FIELD]["key_id"] != new_business_id  # domains remain separated
    business = issue_business_state_seal(state, "rotation-session", MODE_STANDARD, 1)

    _set_ring(monkeypatch, {"old": old, "new": new}, "old", "old")
    # Old/new persisted fingerprints select verification material, independent
    # of the currently selected issuance key.
    verify_graph_state_seal(state, MODE_STANDARD)
    verify_business_state_seal(
        state, "rotation-session", MODE_STANDARD, 1, business, key_id=new_business_id,
    )
    rollback_state = _state()
    rollback_state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(rollback_state, MODE_STANDARD)
    assert rollback_state[GRAPH_SEAL_FIELD]["key_id"] != state[GRAPH_SEAL_FIELD]["key_id"]


def test_removed_old_key_rejects_old_checkpoint_seal(monkeypatch: pytest.MonkeyPatch):
    old = b"o" * 32
    _set_ring(monkeypatch, {"old": old, "new": b"n" * 32}, "new", "old")
    state = _state()
    # Generate an old record, then remove its verification key.
    _set_ring(monkeypatch, {"old": old, "new": b"n" * 32}, "old", "old")
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    _set_ring(monkeypatch, {"new": b"n" * 32}, "new", None)
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_STANDARD)


def test_non_ascii_checkpoint_fingerprint_is_rejected_as_integrity_error(monkeypatch: pytest.MonkeyPatch):
    _set_ring(monkeypatch, {"active": b"a" * 32}, "active", None)
    state = _state()
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    state[GRAPH_SEAL_FIELD]["key_id"] = "０" * 64
    with pytest.raises(CheckpointIntegrityError):
        verify_graph_state_seal(state, MODE_STANDARD)


def test_explicit_snapshot_bindings_are_v3_and_bind_alias(monkeypatch: pytest.MonkeyPatch):
    _set_ring(monkeypatch, {"old": b"o" * 32, "new": b"n" * 32}, "new", "old")
    binding = create_admin_profile_snapshot_binding("s", {"name": "new"}, {"id": 2}, "scenario")
    assert set(binding) == {"version", "algorithm", "key_id", "mac"}
    assert binding["version"] == 3
    assert binding["key_id"] == "new"
    verify_admin_profile_snapshot_binding(binding, "s", {"name": "new"}, {"id": 2}, "scenario")
    float_version = dict(binding)
    float_version["version"] = 3.0
    with pytest.raises(ValueError):
        verify_admin_profile_snapshot_binding(float_version, "s", {"name": "new"}, {"id": 2}, "scenario")
    binding["key_id"] = "old"
    with pytest.raises(ValueError):
        verify_admin_profile_snapshot_binding(binding, "s", {"name": "new"}, {"id": 2}, "scenario")


def test_explicit_ring_never_uses_active_key_to_guess_a_v2_binding(monkeypatch: pytest.MonkeyPatch):
    old = b"o" * 32
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", old.decode("ascii"))
    monkeypatch.delenv("RESEARCH_CHECKPOINT_KEYS_JSON", raising=False)
    monkeypatch.delenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", raising=False)
    monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)
    binding = create_admin_profile_snapshot_binding("s", {"name": "old"}, {"id": 3}, "scenario")

    _set_ring(monkeypatch, {"old": old}, "old", None)
    with pytest.raises(ValueError):
        verify_admin_profile_snapshot_binding(binding, "s", {"name": "old"}, {"id": 3}, "scenario")


def test_keyring_repr_does_not_expose_key_material(monkeypatch: pytest.MonkeyPatch):
    secret = b"do-not-put-this-checkpoint-secret-in-logs"
    _set_ring(monkeypatch, {"active": secret}, "active", None)
    rendered = repr(load_checkpoint_keyring())
    assert secret.decode("ascii") not in rendered
    assert base64.b64encode(secret).decode("ascii") not in rendered


@pytest.mark.parametrize("keys,active,legacy", [
    (None, "new", None),
    ({"new": base64.b64encode(b"n" * 32).decode("ascii")}, None, None),
    ({"new": "%%%"}, "new", None),
    ({"new": base64.b64encode(b"short").decode("ascii")}, "new", None),
])
def test_bad_explicit_configuration_never_falls_back(monkeypatch: pytest.MonkeyPatch, keys, active, legacy):
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "would-have-been-a-fallback")
    if keys is None:
        monkeypatch.delenv("RESEARCH_CHECKPOINT_KEYS_JSON", raising=False)
    else:
        monkeypatch.setenv("RESEARCH_CHECKPOINT_KEYS_JSON", json.dumps(keys))
    if active is None:
        monkeypatch.delenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", raising=False)
    else:
        monkeypatch.setenv("RESEARCH_CHECKPOINT_ACTIVE_KEY_ID", active)
    if legacy is None:
        monkeypatch.delenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", raising=False)
    else:
        monkeypatch.setenv("RESEARCH_CHECKPOINT_LEGACY_KEY_ID", legacy)
    with pytest.raises(CheckpointKeyConfigurationError):
        load_checkpoint_keyring()
