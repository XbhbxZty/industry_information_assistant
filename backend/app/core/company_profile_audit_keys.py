"""Explicit environment configuration for company-profile audit signing keys.

Audit-chain keys intentionally have no relationship to JWT or snapshot-HMAC
settings.  Operators must provide every still-needed verification key during a
rotation, along with the identifier to use for new records.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

try:  # pragma: no cover - supports both application import layouts
    from service.company_profile_audit_integrity import AuditKeyring
except ImportError:  # pragma: no cover
    from app.service.company_profile_audit_integrity import AuditKeyring


class CompanyProfileAuditKeyConfigurationError(ValueError):
    """Safe, operator-facing failure for unavailable audit key configuration."""


class _DuplicateKeyIdError(ValueError):
    pass


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_id, encoded_key in pairs:
        if key_id in result:
            raise _DuplicateKeyIdError
        result[key_id] = encoded_key
    return result


def _load_key_map(raw: str) -> dict[str, bytes]:
    try:
        parsed = json.loads(raw, object_pairs_hook=_strict_object_pairs)
    except (json.JSONDecodeError, TypeError, _DuplicateKeyIdError) as exc:
        raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥配置格式无效") from exc

    if type(parsed) is not dict or not parsed:
        raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥配置不能为空")

    decoded: dict[str, bytes] = {}
    for key_id, encoded_key in parsed.items():
        if not isinstance(key_id, str) or not isinstance(encoded_key, str):
            raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥配置格式无效")
        try:
            material = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥编码无效") from exc
        if len(material) < 32:
            raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥长度不足")
        decoded[key_id] = material
    return decoded


def load_company_profile_audit_keyring() -> AuditKeyring:
    """Load a strict, explicit keyring without any development fallback.

    ``COMPANY_PROFILE_AUDIT_KEYS_JSON`` is a JSON object mapping a key id to a
    standard-base64 key with at least 32 decoded bytes.  The active identifier
    is supplied separately in ``COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID``.
    """
    raw_keys = os.getenv("COMPANY_PROFILE_AUDIT_KEYS_JSON")
    active_key_id = os.getenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID")
    if not raw_keys or not active_key_id:
        raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥配置不可用")

    try:
        return AuditKeyring(
            active_key_id=active_key_id,
            keys=_load_key_map(raw_keys),
        )
    except CompanyProfileAuditKeyConfigurationError:
        raise
    except (TypeError, ValueError) as exc:
        # Do not surface an environment value or library exception: it could
        # contain raw secret material in an operator-provided string.
        raise CompanyProfileAuditKeyConfigurationError("企业档案审计密钥配置无效") from exc
