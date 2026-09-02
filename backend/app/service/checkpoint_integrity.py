"""Versioned HMAC seals for persisted research checkpoints.

This module deliberately has no dependency on ``deep_research_v2``.  The graph
and the database persistence layer are separate trust boundaries, so keeping
the signing primitives here avoids an import cycle and makes the format easy to
audit independently.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID
from typing import Any, Dict, Optional

try:
    from core.checkpoint_keys import (
        CheckpointKeyConfigurationError,
        load_checkpoint_keyring,
    )
except ImportError:  # pragma: no cover - package-root import compatibility
    from app.core.checkpoint_keys import (
        CheckpointKeyConfigurationError,
        load_checkpoint_keyring,
    )


MODE_MANAGED = "managed_v1"
MODE_STANDARD = "standard_v1"
GRAPH_SEAL_FIELD = "checkpoint_graph_seal"
INTEGRITY_VERSION = 1

_ALLOWED_MODES = frozenset((MODE_MANAGED, MODE_STANDARD))
_SEAL_KEYS = frozenset(("version", "algorithm", "mode", "key_id", "mac"))
_ALGORITHM = "hmac-sha256"
_ROOT_CONTEXT = b"research-checkpoint-integrity/v1"
_GRAPH_DOMAIN = b"graph-state"
_BUSINESS_DOMAIN = b"business-state"
_CONTEXT_DOMAIN = b"checkpoint-context"
CHECKPOINT_STATUSES = frozenset(("running", "paused", "completed", "failed"))
CONTEXT_NATIVE = "native_v1"
CONTEXT_MIGRATION = "migration_observed_v1"
_KEY_ID_CONTEXT = b"key-id"
_RUNTIME_COMPANY_PROFILE_MARKER = "_admin_profile_snapshot_authorized"


class CheckpointIntegrityError(ValueError):
    """A checkpoint seal or its persisted metadata failed validation."""


def _require_mode(mode: Any) -> str:
    if not isinstance(mode, str) or mode not in _ALLOWED_MODES:
        raise CheckpointIntegrityError("检查点完整性模式非法")
    return mode


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CheckpointIntegrityError("检查点完整性内容必须是严格 JSON") from exc


def _keyring():
    try:
        return load_checkpoint_keyring()
    except CheckpointKeyConfigurationError as exc:
        raise CheckpointIntegrityError(str(exc)) from exc


def _derived_key(domain: bytes, mode: str, key_id: Optional[str] = None) -> bytes:
    """Derive a purpose- and mode-separated HMAC key from the server secret."""
    _require_mode(mode)
    ring = _keyring()
    selected = ring.active_key_id if key_id is None else key_id
    return hmac.new(
        ring.key_material(selected),
        _ROOT_CONTEXT + b"/" + domain + b"/" + mode.encode("ascii"),
        hashlib.sha256,
    ).digest()


def _key_id(domain: bytes, mode: str, key_id: Optional[str] = None) -> str:
    # Store a non-secret fingerprint of the *derived* key.  It catches domain
    # confusion and makes an accidental configuration/key rotation fail closed.
    return hmac.new(
        _derived_key(domain, mode, key_id), _KEY_ID_CONTEXT, hashlib.sha256
    ).hexdigest()


def _is_hex_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _state_projection(state: Any) -> Dict[str, Any]:
    """Project every business field, excluding only known runtime/seal fields.

    This intentionally is not a hand-maintained allowlist.  Newly added graph
    state becomes integrity-protected automatically.  Top-level underscore
    fields are runtime-only by the existing checkpoint convention.  The one
    company-profile marker is reissued on restore and therefore excluded too.
    """
    if not isinstance(state, dict):
        raise CheckpointIntegrityError("检查点状态必须是对象")

    projection: Dict[str, Any] = {}
    for key, value in state.items():
        if not isinstance(key, str):
            raise CheckpointIntegrityError("检查点状态字段名必须是字符串")
        if key.startswith("_") or key == GRAPH_SEAL_FIELD:
            continue
        if key == "company_profile" and isinstance(value, dict):
            projection[key] = {
                profile_key: profile_value
                for profile_key, profile_value in value.items()
                if profile_key != _RUNTIME_COMPANY_PROFILE_MARKER
            }
        else:
            projection[key] = value
    return projection


def _graph_body(state: Any, mode: str) -> bytes:
    return _canonical_json_bytes({
        "version": INTEGRITY_VERSION,
        "mode": _require_mode(mode),
        "state": _state_projection(state),
    })


def _business_body(state: Any, session_id: Any, mode: str, revision: Any) -> bytes:
    if not isinstance(session_id, str) or not session_id:
        raise CheckpointIntegrityError("检查点完整性缺少合法 session_id")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise CheckpointIntegrityError("检查点完整性业务版本非法")
    return _canonical_json_bytes({
        "version": INTEGRITY_VERSION,
        "session_id": session_id,
        "mode": _require_mode(mode),
        "revision": revision,
        "state": _state_projection(state),
    })


def _seal_for(domain: bytes, body: bytes, mode: str) -> Dict[str, Any]:
    key = _derived_key(domain, mode)
    return {
        "version": INTEGRITY_VERSION,
        "algorithm": _ALGORITHM,
        "mode": mode,
        "key_id": _key_id(domain, mode),
        "mac": hmac.new(key, body, hashlib.sha256).hexdigest(),
    }


def _key_alias_for_fingerprint(domain: bytes, mode: str, fingerprint: Any) -> str:
    if not _is_hex_digest(fingerprint):
        raise CheckpointIntegrityError("检查点图完整性封签密钥标识不一致")
    ring = _keyring()
    for alias in ring.key_ids:
        if hmac.compare_digest(fingerprint, _key_id(domain, mode, alias)):
            return alias
    raise CheckpointIntegrityError("检查点图完整性封签密钥标识不一致")


def _verify_seal(seal: Any, domain: bytes, body: bytes, mode: str) -> None:
    mode = _require_mode(mode)
    if not isinstance(seal, dict) or set(seal) != _SEAL_KEYS:
        raise CheckpointIntegrityError("检查点图完整性封签形状非法")
    if seal.get("version") != INTEGRITY_VERSION:
        raise CheckpointIntegrityError("检查点图完整性封签版本不受支持")
    if seal.get("algorithm") != _ALGORITHM:
        raise CheckpointIntegrityError("检查点图完整性封签算法非法")
    if seal.get("mode") != mode:
        raise CheckpointIntegrityError("检查点图完整性封签模式不一致")
    alias = _key_alias_for_fingerprint(domain, mode, seal.get("key_id"))
    supplied = seal.get("mac")
    if not _is_hex_digest(supplied):
        raise CheckpointIntegrityError("检查点图完整性封签 MAC 非法")
    expected = hmac.new(_derived_key(domain, mode, alias), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise CheckpointIntegrityError("检查点图完整性封签不一致")


def issue_graph_state_seal(state: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """Return the graph-state seal to store under :data:`GRAPH_SEAL_FIELD`."""
    mode = _require_mode(mode)
    return _seal_for(_GRAPH_DOMAIN, _graph_body(state, mode), mode)


def verify_graph_state_seal(state: Dict[str, Any], mode: str) -> None:
    """Fail closed unless the graph state carries a valid, matching seal."""
    mode = _require_mode(mode)
    if not isinstance(state, dict):
        raise CheckpointIntegrityError("检查点状态必须是对象")
    _verify_seal(state.get(GRAPH_SEAL_FIELD), _GRAPH_DOMAIN, _graph_body(state, mode), mode)


def issue_business_state_seal(
    state: Dict[str, Any], session_id: str, mode: str, revision: int,
) -> str:
    """Return the database-business-state MAC for the cleaned persisted state."""
    mode = _require_mode(mode)
    return hmac.new(
        _derived_key(_BUSINESS_DOMAIN, mode),
        _business_body(state, session_id, mode, revision),
        hashlib.sha256,
    ).hexdigest()


def verify_business_state_seal(
    state: Dict[str, Any], session_id: str, mode: str, revision: int, seal: str,
    *, key_id: Optional[str] = None,
) -> None:
    """Verify a database-business-state MAC against its exact persistence context."""
    mode = _require_mode(mode)
    if not _is_hex_digest(seal):
        raise CheckpointIntegrityError("检查点业务完整性 MAC 非法")
    alias = _keyring().active_key_id if key_id is None else _key_alias_for_fingerprint(
        _BUSINESS_DOMAIN, mode, key_id
    )
    expected = hmac.new(
        _derived_key(_BUSINESS_DOMAIN, mode, alias),
        _business_body(state, session_id, mode, revision),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(seal, expected):
        raise CheckpointIntegrityError("检查点业务完整性封签不一致")


def mode_from_state(state: Dict[str, Any]) -> Optional[str]:
    """Get the asserted mode without treating a missing seal as a legacy mode.

    Callers must still run :func:`verify_graph_state_seal`; this helper only
    chooses the claimed mode and detects contradictory private/seal metadata.
    """
    if not isinstance(state, dict):
        raise CheckpointIntegrityError("检查点状态必须是对象")

    private_mode = state.get("_checkpoint_integrity_mode")
    if private_mode is not None:
        private_mode = _require_mode(private_mode)

    graph_seal = state.get(GRAPH_SEAL_FIELD)
    # ``create_initial_state`` predeclares this TypedDict field with ``{}``.
    # It is an unsigned construction-time placeholder, not an attempted valid
    # seal.  A persistence/restore boundary still calls verify_graph_state_seal
    # and therefore rejects it; mode discovery merely needs to let the graph
    # issue its first real seal using the private construction-time mode.
    if graph_seal is None or graph_seal == {}:
        return private_mode
    if not isinstance(graph_seal, dict):
        raise CheckpointIntegrityError("检查点图完整性封签形状非法")
    graph_mode = _require_mode(graph_seal.get("mode"))
    if private_mode is not None and private_mode != graph_mode:
        raise CheckpointIntegrityError("检查点完整性模式声明不一致")
    return private_mode or graph_mode


def business_key_id(mode: str) -> str:
    """Internal persistence helper for the stored business-seal key id."""
    return _key_id(_BUSINESS_DOMAIN, _require_mode(mode))


def _checkpoint_context_body(
    checkpoint_id: Any, session_id: str, owner_id: Any, status: str,
    mode: str, revision: int, business_seal: str, origin: str,
) -> bytes:
    try:
        checkpoint_id = str(UUID(str(checkpoint_id)))
        owner_id = str(UUID(str(owner_id))) if owner_id is not None else None
    except (ValueError, TypeError, AttributeError) as exc:
        raise CheckpointIntegrityError("检查点上下文 UUID 非法") from exc
    if not isinstance(session_id, str) or not session_id or len(session_id) > 64:
        raise CheckpointIntegrityError("检查点上下文 session_id 非法")
    if not isinstance(status, str) or status not in CHECKPOINT_STATUSES:
        raise CheckpointIntegrityError("检查点上下文 status 非法")
    if type(revision) is not int or revision < 1:
        raise CheckpointIntegrityError("检查点上下文业务版本非法")
    if not _is_hex_digest(business_seal):
        raise CheckpointIntegrityError("检查点上下文业务封签非法")
    if origin not in (CONTEXT_NATIVE, CONTEXT_MIGRATION):
        raise CheckpointIntegrityError("检查点上下文来源非法")
    return _canonical_json_bytes({
        "version": 1, "checkpoint_id": checkpoint_id, "session_id": session_id,
        "owner_id": owner_id, "status": status, "mode": _require_mode(mode),
        "business_revision": revision, "business_seal": business_seal,
        "origin": origin,
    })


def issue_checkpoint_context_seal(
    checkpoint_id: Any, session_id: str, owner_id: Any, status: str,
    mode: str, revision: int, business_seal: str, *, origin: str = CONTEXT_NATIVE,
) -> Dict[str, Any]:
    """Bind row identity/ownership/workflow to the existing v1 business MAC.

    This is a separate domain: neither old graph nor business signing bytes
    change. Migration observations explicitly do not claim historical binding.
    """
    body = _checkpoint_context_body(
        checkpoint_id, session_id, owner_id, status, mode, revision, business_seal, origin,
    )
    return {**_seal_for(_CONTEXT_DOMAIN, body, mode), "origin": origin}


def verify_checkpoint_context_seal(
    checkpoint_id: Any, session_id: str, owner_id: Any, status: str,
    mode: str, revision: int, business_seal: str, seal: Any,
) -> None:
    if not isinstance(seal, dict) or set(seal) != _SEAL_KEYS | {"origin"}:
        raise CheckpointIntegrityError("检查点缺少合法上下文封签")
    if type(seal.get("version")) is not int:
        raise CheckpointIntegrityError("检查点上下文封签版本非法")
    body = _checkpoint_context_body(
        checkpoint_id, session_id, owner_id, status, mode, revision, business_seal, seal["origin"],
    )
    _verify_seal({k: v for k, v in seal.items() if k != "origin"}, _CONTEXT_DOMAIN, body, mode)


def describe_checkpoint_verification_keys() -> Dict[str, str]:
    """Map every configured verification fingerprint to its logical alias."""
    ring = _keyring()
    return {
        _key_id(domain, mode, alias): alias
        for alias in ring.key_ids
        for domain in (_GRAPH_DOMAIN, _BUSINESS_DOMAIN, _CONTEXT_DOMAIN)
        for mode in _ALLOWED_MODES
    }
