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
import os
from typing import Any, Dict, Optional


MODE_MANAGED = "managed_v1"
MODE_STANDARD = "standard_v1"
GRAPH_SEAL_FIELD = "checkpoint_graph_seal"
INTEGRITY_VERSION = 1

_ALLOWED_MODES = frozenset((MODE_MANAGED, MODE_STANDARD))
_SEAL_KEYS = frozenset(("version", "algorithm", "mode", "key_id", "mac"))
_ALGORITHM = "hmac-sha256"
_SECRET_ENV = "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY"
_ROOT_CONTEXT = b"research-checkpoint-integrity/v1"
_GRAPH_DOMAIN = b"graph-state"
_BUSINESS_DOMAIN = b"business-state"
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


def _server_secret() -> bytes:
    secret = os.getenv(_SECRET_ENV) or os.getenv("JWT_SECRET_KEY")
    if not isinstance(secret, str) or not secret:
        raise CheckpointIntegrityError(
            f"缺少服务端 {_SECRET_ENV} 或 JWT_SECRET_KEY，拒绝处理检查点完整性"
        )
    return secret.encode("utf-8")


def _derived_key(domain: bytes, mode: str) -> bytes:
    """Derive a purpose- and mode-separated HMAC key from the server secret."""
    _require_mode(mode)
    return hmac.new(
        _server_secret(),
        _ROOT_CONTEXT + b"/" + domain + b"/" + mode.encode("ascii"),
        hashlib.sha256,
    ).digest()


def _key_id(domain: bytes, mode: str) -> str:
    # Store a non-secret fingerprint of the *derived* key.  It catches domain
    # confusion and makes an accidental configuration/key rotation fail closed.
    return hmac.new(
        _derived_key(domain, mode), _KEY_ID_CONTEXT, hashlib.sha256
    ).hexdigest()


def _is_hex_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


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
    if seal.get("key_id") != _key_id(domain, mode):
        raise CheckpointIntegrityError("检查点图完整性封签密钥标识不一致")
    supplied = seal.get("mac")
    if not _is_hex_digest(supplied):
        raise CheckpointIntegrityError("检查点图完整性封签 MAC 非法")
    expected = hmac.new(_derived_key(domain, mode), body, hashlib.sha256).hexdigest()
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
) -> None:
    """Verify a database-business-state MAC against its exact persistence context."""
    mode = _require_mode(mode)
    if not _is_hex_digest(seal):
        raise CheckpointIntegrityError("检查点业务完整性 MAC 非法")
    expected = issue_business_state_seal(state, session_id, mode, revision)
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
