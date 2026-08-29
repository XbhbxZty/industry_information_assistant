"""SQLite regression coverage for administrator-maintained company profiles.

These tests deliberately create only the two Stage-3 tables.  The production
``User`` model uses PostgreSQL UUID types, whereas the profile tables are
required to be independently portable to SQLite for service-level regression
tests.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (os.fspath(BACKEND), os.fspath(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database import get_db  # noqa: E402
from models.company_profile import AdminCompanyProfile, AdminCompanyProfileAudit  # noqa: E402
from router.auth_router import get_current_user_required, require_superuser  # noqa: E402
from router.company_profile_router import router  # noqa: E402
from schemas.company_profile import CompanyProfileCreate, CompanyProfileUpdate  # noqa: E402
from service.admin_company_profile_service import (  # noqa: E402
    AdminCompanyProfileConflict,
    AdminCompanyProfileNotFound,
    AdminCompanyProfileValidationError,
    archive_company_profile,
    company_profile_templates,
    create_company_profile,
    get_active_profile_snapshot,
    get_company_profile,
    prepare_company_profile_content,
    search_profile_materials,
    update_company_profile,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AdminCompanyProfile.__table__.create(engine)
    AdminCompanyProfileAudit.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        AdminCompanyProfileAudit.__table__.drop(engine)
        AdminCompanyProfile.__table__.drop(engine)
        engine.dispose()


def _write(name: str = "测试企业有限公司", **overrides) -> CompanyProfileCreate:
    body = {
        "profile": {"name": name},
        "scenario": "",
        "scenario_data": {},
        "field_sources": [],
        "materials": [],
    }
    body.update(overrides)
    return CompanyProfileCreate(**body)


def _trusted_registration_source(*field_ids: str) -> dict:
    return {
        "source_id": "registry-1",
        "name": "国家企业信用信息公示系统",
        "issuer": "市场监管部门",
        "source_type": "official",
        "field_ids": list(field_ids),
        "retrieved_at": "2026-08-20T10:00:00+00:00",
        "as_of_date": "2026-08-19",
        "reference": "https://example.test/registry/1",
    }


def test_schema_requires_only_name_and_rejects_custom_required():
    assert _write().profile["name"] == "测试企业有限公司"
    with pytest.raises(Exception, match="profile.name"):
        CompanyProfileCreate(profile={})

    with pytest.raises(AdminCompanyProfileValidationError, match="required"):
        prepare_company_profile_content(_write(profile={"name": "甲", "required": []}))


def test_templates_explain_server_owned_coverage_and_fixed_required_semantics():
    template = company_profile_templates()
    assert template["scenario_options"] == ["", "factoring"]
    assert "factoring" in template["scenario_data_keys"]
    assert "自动重建" in template["coverage"]
    assert "不接受" in template["required"]


def test_service_crud_audit_conflict_archive_and_snapshot_hash(db):
    created = create_company_profile(db, _write(), actor_id="admin-1", change_reason="初始录入")
    assert created.revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 1
    assert db.query(AdminCompanyProfileAudit).one().action == "created"

    changed = CompanyProfileUpdate(
        profile={"name": "已更新企业有限公司"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=1, change_reason="更正名称",
    )
    updated = update_company_profile(
        db, created.id, changed, expected_revision=1, actor_id="admin-2", change_reason="更正名称",
    )
    assert updated.revision == 2
    audit = db.query(AdminCompanyProfileAudit).order_by(AdminCompanyProfileAudit.revision).all()
    assert [entry.action for entry in audit] == ["created", "updated"]
    assert audit[1].before_snapshot["name"] == "测试企业有限公司"
    assert audit[1].after_snapshot["name"] == "已更新企业有限公司"

    with pytest.raises(AdminCompanyProfileConflict):
        update_company_profile(
            db, created.id, changed, expected_revision=1, actor_id="admin-3", change_reason="陈旧提交",
        )

    archived = archive_company_profile(
        db, created.id, expected_revision=2, actor_id="admin-1", change_reason="不再使用",
    )
    assert archived.status == "archived"
    assert archived.revision == 3
    assert db.query(AdminCompanyProfileAudit).count() == 3
    with pytest.raises(AdminCompanyProfileNotFound):
        get_company_profile(db, created.id)
    with pytest.raises(AdminCompanyProfileNotFound):
        get_active_profile_snapshot(db, created.id)


def test_source_gate_rebuilds_coverage_and_preserves_empty_semantics(db):
    # A non-empty registration would become verified.  It cannot be saved
    # until a trusted source explicitly covers every derived verified field.
    profile = {
        "name": "来源闸门有限公司",
        "registration": {"company_type": "有限责任公司", "operating_status": "存续"},
        "coverage": {"queried": ["dishonesty"], "retrieved_at": "2099-01-01"},
    }
    with pytest.raises(AdminCompanyProfileValidationError, match="缺少受信"):
        create_company_profile(
            db,
            _write(profile=profile, field_sources=[_trusted_registration_source("registration")]),
            actor_id="admin", change_reason="应失败",
        )

    created = create_company_profile(
        db,
        _write(
            profile=profile,
            field_sources=[_trusted_registration_source(
                "registration", "operating_status",
            )],
        ),
        actor_id="admin", change_reason="可信来源录入",
    )
    assert created.profile_json["coverage"] == {
        "queried": ["operating_status", "registration"],
        "retrieved_at": "2026-08-20T10:00:00+00:00",
    }

    # A queried event with no facts remains the production no-record outcome;
    # an empty attribute is not converted into the same positive conclusion.
    event = prepare_company_profile_content(_write(
        profile={"name": "事件空结果有限公司"},
        field_sources=[_trusted_registration_source("dishonesty")],
    ))
    assert event["profile"]["coverage"]["queried"] == ["dishonesty"]


def test_materials_are_profile_scoped_scoreless_and_never_structured_evidence(db):
    material = {
        "material_id": "m-1",
        "source_type": "company_submitted",
        "title": "企业说明",
        "content": "本企业的保理应收账款来自长期客户。",
        "reference": "企业邮件 2026-08-20",
        "date_unknown_reason": "提交材料未标注事实日期",
    }
    created = create_company_profile(
        db, _write(materials=[material]), actor_id="admin", change_reason="补充背景材料",
    )
    result = search_profile_materials(db, created.id, query="保理 应收账款", limit=10)
    assert result["total"] == 1
    assert result["items"][0]["eligible_for_structured_evidence"] is False
    assert "score" not in result["items"][0]

    other = create_company_profile(
        db, _write(name="隔离企业有限公司"), actor_id="admin", change_reason="第二档案",
    )
    assert search_profile_materials(db, other.id, query="保理", limit=10)["items"] == []


def test_snapshot_is_decorated_json_and_detects_persisted_tampering(db):
    created = create_company_profile(
        db,
        _write(
            scenario="factoring",
            scenario_data={"accounts_receivable_gross": {"value": 1200}},
            field_sources=[_trusted_registration_source("accounts_receivable_gross")],
        ),
        actor_id="admin", change_reason="保理档案",
    )
    snapshot = get_active_profile_snapshot(db, created.id)
    assert set(snapshot) == {"profile", "ref", "scenario"}
    assert snapshot["scenario"] == "factoring"
    assert snapshot["ref"] == snapshot["profile"]["_admin_profile_ref"]
    assert snapshot["ref"]["source"] == "admin_company_profile"
    assert snapshot["profile"]["_scenario_data"] == {
        "accounts_receivable_gross": {"value": 1200},
    }
    snapshot["profile"]["name"] = "调用方篡改"
    assert get_company_profile(db, created.id).profile_json["name"] == "测试企业有限公司"

    db.execute(update(AdminCompanyProfile).where(AdminCompanyProfile.id == created.id).values(
        profile_json={"name": "数据库篡改"},
    ))
    db.commit()
    with pytest.raises(AdminCompanyProfileValidationError, match="哈希"):
        get_active_profile_snapshot(db, created.id)


def test_router_rbac_and_sqlite_crud_flow(db):
    app = FastAPI()
    app.include_router(router)
    normal = SimpleNamespace(id="user-1", is_superuser=False)
    admin = SimpleNamespace(id="admin-1", is_superuser=True)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_required] = lambda: normal
    app.dependency_overrides[require_superuser] = lambda: admin
    client = TestClient(app)

    assert client.get("/company-profiles/templates").status_code == 200
    create_response = client.post("/company-profiles", json={"profile": {"name": "API 企业"}})
    assert create_response.status_code == 201
    profile_id = create_response.json()["id"]
    assert client.get(f"/company-profiles/{profile_id}").status_code == 200

    # Restore the real dependency to verify its direct 403 guard rather than
    # merely trusting the route declaration.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_superuser(normal))
    assert exc_info.value.status_code == 403

    stale = client.put(f"/company-profiles/{profile_id}", json={
        "profile": {"name": "API 企业 2"}, "expected_revision": 0, "change_reason": "测试",
    })
    assert stale.status_code == 422  # schema disallows a non-positive revision
    first_update = client.put(f"/company-profiles/{profile_id}", json={
        "profile": {"name": "API 企业 2"}, "expected_revision": 1, "change_reason": "测试",
    })
    assert first_update.status_code == 200
    conflict = client.put(f"/company-profiles/{profile_id}", json={
        "profile": {"name": "API 企业 3"}, "expected_revision": 1, "change_reason": "陈旧",
    })
    assert conflict.status_code == 409

