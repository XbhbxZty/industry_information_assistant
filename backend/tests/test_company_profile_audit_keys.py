"""Tests for the explicit company-profile audit keyring provider."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (os.fspath(BACKEND), os.fspath(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.company_profile_audit_keys import (
    CompanyProfileAuditKeyConfigurationError,
    load_company_profile_audit_keyring,
)


def _encoded(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def test_loads_explicit_active_key_and_rotation_history(monkeypatch):
    monkeypatch.setenv(
        "COMPANY_PROFILE_AUDIT_KEYS_JSON",
        json.dumps({"audit-2026": _encoded(1), "audit-2027": _encoded(2)}),
    )
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", "audit-2027")

    keyring = load_company_profile_audit_keyring()

    assert keyring.active_key_id == "audit-2027"
    assert keyring.key_ids == frozenset({"audit-2026", "audit-2027"})


@pytest.mark.parametrize(
    ("keys", "active"),
    [
        (None, None),
        ("", "audit-2026"),
        ("{}", "audit-2026"),
        ('{"audit-2026":"not base64!"}', "audit-2026"),
        (json.dumps({"audit-2026": base64.b64encode(b"short").decode()}), "audit-2026"),
        ('{"audit-2026":"' + _encoded(1) + '","audit-2026":"' + _encoded(2) + '"}', "audit-2026"),
        (json.dumps({"audit-2026": _encoded(1)}), "missing"),
    ],
)
def test_rejects_missing_malformed_duplicate_weak_or_unknown_configuration(
    monkeypatch, keys, active,
):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", raising=False)
    if keys is None:
        monkeypatch.delenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", raising=False)
    else:
        monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", keys)
    if active is None:
        monkeypatch.delenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", raising=False)
    else:
        monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", active)

    with pytest.raises(CompanyProfileAuditKeyConfigurationError):
        load_company_profile_audit_keyring()


def test_never_uses_jwt_or_snapshot_secret_as_a_fallback(monkeypatch):
    monkeypatch.delenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", raising=False)
    monkeypatch.delenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret-must-not-be-an-audit-key")
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "snapshot-secret-must-not-be-an-audit-key")

    with pytest.raises(CompanyProfileAuditKeyConfigurationError):
        load_company_profile_audit_keyring()
