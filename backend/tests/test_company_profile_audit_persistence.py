"""Actual CRUD/HTTP boundaries must consume the complete observed HMAC chain."""
from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_admin_company_profiles import db, _router_client, _write  # noqa: F401
from models.company_profile import AdminCompanyProfile, AdminCompanyProfileAudit
from service import admin_company_profile_service as service
from service.company_profile_audit_integrity import snapshot_sha256


def _create(db):
    return service.create_company_profile(db, _write(), actor_id="admin", change_reason="create")


def _client(db):
    return _router_client(db, SimpleNamespace(id="admin", is_active=True, is_superuser=True))


def test_actual_crud_persists_genesis_and_continuous_signed_head(db):
    row = _create(db)
    service.update_company_profile(
        db, row.id, _write(name="second version"), expected_revision=1,
        actor_id="admin2", change_reason="update",
    )
    service.archive_company_profile(db, row.id, expected_revision=2, actor_id="admin", change_reason="archive")
    audits = service.validate_company_profile_audit_history(db, row.id)
    assert [a.action for a in audits] == ["created", "updated", "archived"]
    assert [a.key_id for a in audits] == ["test-audit-v1"] * 3
    assert all(a.chain_start == "genesis" for a in audits)
    assert audits[0].previous_audit_mac is None
    for previous, current in zip(audits, audits[1:]):
        assert current.previous_audit_mac == previous.audit_mac
        assert current.before_snapshot_sha256 == previous.after_snapshot_sha256
    assert row.audit_head_mac == audits[-1].audit_mac
    assert row.audit_head_snapshot_sha256 == snapshot_sha256(audits[-1].after_snapshot)


@pytest.mark.parametrize("tamper", ["recompute_all_sha", "actor", "reason", "mac", "head", "unsigned"])
def test_recomputed_plain_hashes_and_metadata_forgery_cannot_pass(db, tamper):
    row = _create(db)
    audit = db.query(AdminCompanyProfileAudit).one()
    if tamper == "recompute_all_sha":
        values = service.prepare_company_profile_content(_write(name="forged business data"))
        row.profile_json, row.name = values["profile"], values["name"]
        row.content_sha256 = values["content_sha256"]
        after = {**copy.deepcopy(audit.after_snapshot), "profile": row.profile_json,
                 "name": row.name, "content_sha256": row.content_sha256}
        audit.after_snapshot, audit.content_sha256 = after, row.content_sha256
        audit.after_snapshot_sha256 = snapshot_sha256(after)
        row.audit_head_snapshot_sha256 = audit.after_snapshot_sha256
        # All non-secret hashes are recomputed; the original MAC remains.
    elif tamper == "actor":
        row.created_by = row.updated_by = audit.actor_id = "forged-admin"
    elif tamper == "reason":
        audit.change_reason = "forged reason"
    elif tamper == "mac":
        row.audit_head_mac = audit.audit_mac = "f" * 64
    elif tamper == "head":
        row.audit_head_snapshot_sha256 = "f" * 64
    else:
        for column in service._AUDIT_SEAL_COLUMNS:
            setattr(audit, column, None)
        row.audit_head_mac = row.audit_head_snapshot_sha256 = None
    db.commit()
    # Deliberately show the former, structural-only boundary still passes.
    service._validate_structural_audit_history(db, row.id)
    with pytest.raises(service.AdminCompanyProfileIntegrityError):
        service.get_company_profile(db, row.id)


@pytest.mark.parametrize("endpoint", ["detail", "list", "history", "materials", "update", "archive", "research"])
def test_corrupt_chain_is_409_at_every_consumption_boundary(db, endpoint):
    row = _create(db)
    db.query(AdminCompanyProfileAudit).one().change_reason = "forged reason"
    db.commit()
    client = _client(db)
    base = f"/company-profiles/{row.id}"
    if endpoint == "research":
        from router.research_router import _load_admin_company_profile_snapshot
        with pytest.raises(HTTPException) as raised:
            _load_admin_company_profile_snapshot(db, row.id)
        assert raised.value.status_code == 409
        return
    if endpoint == "update":
        response = client.put(base, json={"profile": {"name": "new"}, "expected_revision": 1, "change_reason": "update"})
    elif endpoint == "archive":
        response = client.post(base + "/archive", json={"expected_revision": 1, "change_reason": "archive"})
    elif endpoint == "materials":
        response = client.post(base + "/materials/search", json={"query": "contract"})
    else:
        response = client.get({"detail": base, "list": "/company-profiles", "history": base + "/history"}[endpoint])
    assert response.status_code == 409
    assert "测试企业" not in response.text
    assert db.get(AdminCompanyProfile, row.id).revision == 1


@pytest.mark.parametrize("configuration", ["missing", "unknown_historical_key"])
def test_missing_or_unknown_key_returns_503_without_releasing_body(db, monkeypatch, configuration):
    row = _create(db)
    if configuration == "missing":
        monkeypatch.delenv("COMPANY_PROFILE_AUDIT_KEYS_JSON")
        monkeypatch.delenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID")
    else:
        monkeypatch.setenv("COMPANY_PROFILE_AUDIT_KEYS_JSON", json.dumps({
            "other-key": base64.b64encode(b"x" * 32).decode("ascii"),
        }))
        monkeypatch.setenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID", "other-key")
    client = _client(db)
    base = f"/company-profiles/{row.id}"
    responses = [client.get(base), client.get("/company-profiles"), client.get(base + "/history"),
                 client.post(base + "/materials/search", json={"query": "contract"}),
                 client.put(base, json={"profile": {"name": "new"}, "expected_revision": 1, "change_reason": "update"}),
                 client.post(base + "/archive", json={"expected_revision": 1, "change_reason": "archive"})]
    assert all(response.status_code == 503 for response in responses)
    assert all("测试企业" not in response.text for response in responses)
    from router.research_router import _load_admin_company_profile_snapshot
    with pytest.raises(HTTPException) as raised:
        _load_admin_company_profile_snapshot(db, row.id)
    assert raised.value.status_code == 503
    assert db.get(AdminCompanyProfile, row.id).revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 1


def test_missing_signing_config_rolls_back_new_profile_and_audit(db, monkeypatch):
    monkeypatch.delenv("COMPANY_PROFILE_AUDIT_KEYS_JSON")
    response = _client(db).post("/company-profiles", json={"profile": {"name": "must rollback"}, "change_reason": "create"})
    assert response.status_code == 503
    assert db.query(AdminCompanyProfile).count() == db.query(AdminCompanyProfileAudit).count() == 0


@pytest.mark.parametrize("action", ["create", "update", "archive"])
def test_final_verification_failure_rolls_back_profile_audit_and_head(db, monkeypatch, action):
    row = _create(db) if action != "create" else None
    original_head = row.audit_head_mac if row else None
    real_verify = service.verify_observed_audit_chain

    def reject_new_revision(observations, **kwargs):
        if kwargs["expected_head"].revision == (1 if action == "create" else 2):
            raise service.CompanyProfileAuditIntegrityError("injected final verification failure")
        return real_verify(observations, **kwargs)

    monkeypatch.setattr(service, "verify_observed_audit_chain", reject_new_revision)
    with pytest.raises(service.AdminCompanyProfileIntegrityError):
        if action == "create":
            _create(db)
        elif action == "update":
            service.update_company_profile(db, row.id, _write(name="must rollback"), expected_revision=1, actor_id="admin", change_reason="update")
        else:
            service.archive_company_profile(db, row.id, expected_revision=1, actor_id="admin", change_reason="archive")
    db.expire_all()
    assert db.query(AdminCompanyProfileAudit).count() == (0 if row is None else 1)
    if row is None:
        assert db.query(AdminCompanyProfile).count() == 0
    else:
        restored = service.get_company_profile(db, row.id)
        assert restored.revision == 1 and restored.status == "active"
        assert restored.audit_head_mac == original_head
