"""Configuration-backed keyring for research checkpoint integrity.

The keyring deliberately exposes aliases but never serializes or reports key
material.  A partially supplied rotation configuration is an error: silently
falling back to a server secret in that situation would make a bad rollout
look successful while producing unrecoverable checkpoints.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional


_KEYS_ENV = "RESEARCH_CHECKPOINT_KEYS_JSON"
_ACTIVE_ENV = "RESEARCH_CHECKPOINT_ACTIVE_KEY_ID"
_LEGACY_ENV = "RESEARCH_CHECKPOINT_LEGACY_KEY_ID"
_FALLBACK_ENV = "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII)


class CheckpointKeyConfigurationError(ValueError):
    """Checkpoint key configuration is absent, incomplete, or unsafe."""


@dataclass(frozen=True)
class CheckpointKeyring:
    active_key_id: str
    key_ids: frozenset[str]
    legacy_key_id: Optional[str]
    explicit: bool
    _keys: Mapping[str, bytes] = field(repr=False, compare=False)

    def key_material(self, key_id: str) -> bytes:
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise CheckpointKeyConfigurationError("检查点密钥标识不可用")
        return self._keys[key_id]


def _configuration_error() -> CheckpointKeyConfigurationError:
    # Keep this deliberately generic: environment configuration errors must not
    # reveal aliases, JSON, base64 payloads, or secret material in logs.
    return CheckpointKeyConfigurationError("检查点密钥轮换配置非法")


def _fallback_keyring() -> CheckpointKeyring:
    secret = os.getenv(_FALLBACK_ENV) or os.getenv("JWT_SECRET_KEY")
    if not isinstance(secret, str) or not secret:
        raise CheckpointKeyConfigurationError(
            f"缺少服务端 {_FALLBACK_ENV} 或 JWT_SECRET_KEY，拒绝处理检查点完整性"
        )
    return CheckpointKeyring(
        active_key_id="legacy",
        key_ids=frozenset(("legacy",)),
        legacy_key_id="legacy",
        explicit=False,
        _keys={"legacy": secret.encode("utf-8")},
    )


def load_checkpoint_keyring() -> CheckpointKeyring:
    """Load a validated keyring, or the exact pre-rotation fallback key.

    Presence (including an empty string) of *any* rotation variable opts into
    explicit mode.  Explicit mode consequently never falls back to JWT or the
    older profile-snapshot secret.
    """
    raw_keys = os.getenv(_KEYS_ENV)
    active = os.getenv(_ACTIVE_ENV)
    legacy = os.getenv(_LEGACY_ENV)
    if raw_keys is None and active is None and legacy is None:
        return _fallback_keyring()

    if not isinstance(raw_keys, str) or not isinstance(active, str):
        raise _configuration_error()
    def _no_duplicate_object(pairs):
        result = {}
        for alias, value in pairs:
            if alias in result:
                raise _configuration_error()
            result[alias] = value
        return result

    try:
        parsed = json.loads(raw_keys, object_pairs_hook=_no_duplicate_object)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _configuration_error() from exc
    if not isinstance(parsed, dict) or not parsed or not _ALIAS_RE.fullmatch(active):
        raise _configuration_error()
    if legacy is not None and (not _ALIAS_RE.fullmatch(legacy) or not legacy):
        raise _configuration_error()

    decoded: dict[str, bytes] = {}
    materials: set[bytes] = set()
    for alias, encoded in parsed.items():
        if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
            raise _configuration_error()
        if not isinstance(encoded, str) or not encoded:
            raise _configuration_error()
        try:
            material = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _configuration_error() from exc
        if not material or material in materials:
            raise _configuration_error()
        decoded[alias] = material
        materials.add(material)

    if active not in decoded or (legacy is not None and legacy not in decoded):
        raise _configuration_error()
    # Short historical secrets are accepted only for the one explicitly named
    # legacy alias; an active legacy alias is useful during the first rollout.
    for alias, material in decoded.items():
        if len(material) < 32 and alias != legacy:
            raise _configuration_error()
    return CheckpointKeyring(
        active_key_id=active,
        key_ids=frozenset(decoded),
        legacy_key_id=legacy,
        explicit=True,
        _keys=decoded,
    )
