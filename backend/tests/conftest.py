"""Shared test-only server configuration.

Production loads its checkpoint HMAC secret from the environment.  The test
suite previously needed no signing key because only managed-profile tests used
one; checkpoint integrity now also seals ordinary LangGraph runs.  Give every
test a deterministic non-production key while still allowing individual tests
to delete or replace it when exercising fail-closed configuration behavior.
"""
from __future__ import annotations

import os
import base64
import json

import pytest


@pytest.fixture
def audit_signing_key(monkeypatch: pytest.MonkeyPatch):
    """Explicit opt-in test-only profile audit key; no application default."""
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", "test-audit-v1")
    monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps({
        "test-audit-v1": base64.b64encode(b"test-only-profile-audit-key-32bytes").decode("ascii"),
    }))


@pytest.fixture(autouse=True)
def _test_checkpoint_hmac_key(monkeypatch: pytest.MonkeyPatch):
    if not os.getenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY") and not os.getenv("JWT_SECRET_KEY"):
        monkeypatch.setenv(
            "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY",
            "test-only-research-checkpoint-integrity-key",
        )
