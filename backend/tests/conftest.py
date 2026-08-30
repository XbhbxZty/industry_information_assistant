"""Shared test-only server configuration.

Production loads its checkpoint HMAC secret from the environment.  The test
suite previously needed no signing key because only managed-profile tests used
one; checkpoint integrity now also seals ordinary LangGraph runs.  Give every
test a deterministic non-production key while still allowing individual tests
to delete or replace it when exercising fail-closed configuration behavior.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _test_checkpoint_hmac_key(monkeypatch: pytest.MonkeyPatch):
    if not os.getenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY") and not os.getenv("JWT_SECRET_KEY"):
        monkeypatch.setenv(
            "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY",
            "test-only-research-checkpoint-integrity-key",
        )
