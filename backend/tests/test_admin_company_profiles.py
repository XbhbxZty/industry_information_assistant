"""SQLite regression coverage for administrator-maintained company profiles.

These tests deliberately create only the three profile/audit tables. The production
``User`` model uses PostgreSQL UUID types, whereas the profile tables are
required to be independently portable to SQLite for service-level regression
tests.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (os.fspath(BACKEND), os.fspath(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database import get_db  # noqa: E402
from config.dd_checklist import CHECKLIST, SCENARIO_CHECKLISTS  # noqa: E402
from models.company_profile import (  # noqa: E402
    AdminCompanyProfile, AdminCompanyProfileAudit, AdminCompanyProfileAuditAnchor,
)
from router.auth_router import get_current_user_required, require_superuser  # noqa: E402
from router.company_profile_router import router  # noqa: E402
from schemas.company_profile import (  # noqa: E402
    CompanyProfileCreate,
    CompanyProfileUpdate,
    FieldSourceResponse,
    MaterialResponse,
)
from service.admin_company_profile_service import (  # noqa: E402
    AdminCompanyProfileConflict,
    AdminCompanyProfileIntegrityError,
    AdminCompanyProfileNotFound,
    AdminCompanyProfileValidationError,
    archive_company_profile,
    company_profile_templates,
    create_company_profile,
    get_active_profile_snapshot,
    get_company_profile,
    list_company_profile_history,
    prepare_company_profile_content,
    search_profile_materials,
    update_company_profile,
    validate_company_profile_audit_history,
)


@pytest.fixture()
def db(audit_signing_key):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AdminCompanyProfile.__table__.create(engine)
    AdminCompanyProfileAudit.__table__.create(engine)
    AdminCompanyProfileAuditAnchor.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        AdminCompanyProfileAuditAnchor.__table__.drop(engine)
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


def _router_client(db, user) -> TestClient:
    """Build a client that exercises the real ``require_superuser`` chain."""
    app = FastAPI()
    app.include_router(router)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_required] = lambda: user
    return TestClient(app)


def _anonymous_router_client(db) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_schema_requires_only_name_and_rejects_custom_required():
    assert _write().profile["name"] == "测试企业有限公司"
    with pytest.raises(Exception, match="profile.name"):
        CompanyProfileCreate(profile={})

    with pytest.raises(AdminCompanyProfileValidationError, match="required"):
        prepare_company_profile_content(_write(profile={"name": "甲", "required": []}))


def test_templates_are_checklist_derived_and_explain_server_owned_semantics():
    template = company_profile_templates()
    assert template["version"] == "company-profile-template-v1"
    assert [field["field_id"] for field in template["core"]] == [
        item.field_id for item in CHECKLIST
    ]
    assert [field["field_id"] for field in template["scenarios"]["factoring"]] == [
        item.field_id for item in SCENARIO_CHECKLISTS["factoring"]
    ]
    assert all(
        set(field) == {
            "field_id", "field_name", "category", "required", "description", "scope",
            "input_type",
        }
        and field["scope"] == "core"
        for field in template["core"]
    )
    factoring = {field["field_id"]: field for field in template["scenarios"]["factoring"]}
    assert factoring["accounts_receivable_gross"]["input_type"] == "number"
    assert factoring["accounts_receivable_aging"]["input_type"] == "text"
    assert template["scenario_options"] == ["", *SCENARIO_CHECKLISTS]
    assert template["scenario_data_keys"]["factoring"] == [
        item.field_id for item in SCENARIO_CHECKLISTS["factoring"]
    ]
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


def test_source_rejects_fact_date_later_than_retrieval_time():
    source = _trusted_registration_source("registration")
    source["retrieved_at"] = "2026-08-20T10:00:00+00:00"
    source["as_of_date"] = "2026-08-21"

    with pytest.raises(
        AdminCompanyProfileValidationError,
        match="as_of_date 不得晚于 retrieved_at",
    ):
        prepare_company_profile_content(_write(field_sources=[source]))


def test_source_date_uses_retrieval_local_calendar_day_at_timezone_boundary():
    source = _trusted_registration_source("registration")
    source["retrieved_at"] = "2026-08-20T00:30:00+08:00"
    source["as_of_date"] = "2026-08-20"

    content = prepare_company_profile_content(_write(field_sources=[source]))
    assert content["field_sources"][0]["as_of_date"] == "2026-08-20"


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
    with pytest.raises(AdminCompanyProfileValidationError, match="当前版本|哈希"):
        get_active_profile_snapshot(db, created.id)


def test_audit_writes_require_attributed_actor_and_reason(db):
    for actor_id, reason in ((None, "有效原因"), ("  ", "有效原因"), ("admin", "  ")):
        with pytest.raises(AdminCompanyProfileValidationError, match="审计"):
            create_company_profile(
                db, _write(), actor_id=actor_id, change_reason=reason,
            )
    assert db.query(AdminCompanyProfile).count() == 0
    assert db.query(AdminCompanyProfileAudit).count() == 0

    for invalid_actor in (True, 7, [], {}):
        with pytest.raises(AdminCompanyProfileValidationError, match="actor_id.*字符串"):
            create_company_profile(
                db, _write(), actor_id=invalid_actor, change_reason="有效原因",
            )
    for invalid_reason in (True, 7, [], {}):
        with pytest.raises(AdminCompanyProfileValidationError, match="change_reason.*字符串"):
            create_company_profile(
                db, _write(), actor_id="admin-1", change_reason=invalid_reason,
            )

    created = create_company_profile(
        db, _write(), actor_id="admin-1", change_reason="有效创建",
    )
    changed = CompanyProfileUpdate(
        profile={"name": "更新后企业"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=1, change_reason="有效更新",
    )
    with pytest.raises(AdminCompanyProfileValidationError, match="actor_id"):
        update_company_profile(
            db, created.id, changed, expected_revision=1,
            actor_id=None, change_reason="有效更新",
        )
    with pytest.raises(AdminCompanyProfileValidationError, match="change_reason"):
        archive_company_profile(
            db, created.id, expected_revision=1,
            actor_id="admin-1", change_reason=" ",
        )
    db.expire_all()
    assert get_company_profile(db, created.id).revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 1


def test_fresh_schema_enforces_one_attributed_audit_per_revision(db):
    created = create_company_profile(
        db, _write(), actor_id="admin-1", change_reason="唯一审计",
    )
    original = db.query(AdminCompanyProfileAudit).one()
    db.add(AdminCompanyProfileAudit(
        profile_id=str(created.id), revision=1, action="created",
        actor_id="admin-2", change_reason="伪造重复修订",
        before_snapshot=None, after_snapshot=original.after_snapshot,
        content_sha256=original.content_sha256,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    for actor_id, change_reason in ((" ", "有效原因"), ("admin-2", " ")):
        db.add(AdminCompanyProfileAudit(
            profile_id=f"invalid-{actor_id!r}-{change_reason!r}", revision=1,
            action="created", actor_id=actor_id, change_reason=change_reason,
            before_snapshot=None, after_snapshot=original.after_snapshot,
            content_sha256=original.content_sha256,
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    assert AdminCompanyProfileAudit.__table__.c.actor_id.nullable is False
    assert AdminCompanyProfile.__table__.c.created_by.nullable is False
    assert AdminCompanyProfile.__table__.c.updated_by.nullable is False


def test_audit_continuity_failure_blocks_history_and_research_snapshot(db):
    created = create_company_profile(
        db, _write(), actor_id="admin-1", change_reason="初始版本",
    )
    changed = CompanyProfileUpdate(
        profile={"name": "连续性企业（二版）"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=1, change_reason="第二版",
    )
    update_company_profile(
        db, created.id, changed, expected_revision=1,
        actor_id="admin-2", change_reason="第二版",
    )
    assert [row.revision for row in list_company_profile_history(db, created.id)] == [2, 1]

    second = db.query(AdminCompanyProfileAudit).filter(
        AdminCompanyProfileAudit.revision == 2,
    ).one()
    second.before_snapshot = {**second.before_snapshot, "name": "被篡改的前序名称"}
    db.commit()
    with pytest.raises(AdminCompanyProfileValidationError, match="前后快照不连续"):
        validate_company_profile_audit_history(db, created.id)
    with pytest.raises(AdminCompanyProfileValidationError, match="前后快照不连续"):
        get_active_profile_snapshot(db, created.id)

    rejected = CompanyProfileUpdate(
        profile={"name": "损坏链上不应落盘的第三版"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=2, change_reason="第三版",
    )
    with pytest.raises(AdminCompanyProfileIntegrityError, match="前后快照不连续"):
        update_company_profile(
            db, created.id, rejected, expected_revision=2,
            actor_id="admin-3", change_reason="第三版",
        )
    db.expire_all()
    assert db.get(AdminCompanyProfile, created.id).revision == 2
    with pytest.raises(AdminCompanyProfileIntegrityError):
        get_company_profile(db, created.id)
    assert db.query(AdminCompanyProfileAudit).count() == 2

    admin_client = _router_client(
        db, SimpleNamespace(id="admin-1", is_active=True, is_superuser=True),
    )
    history = admin_client.get(f"/company-profiles/{created.id}/history")
    assert history.status_code == 409


@pytest.mark.parametrize("malformation", ["string_revision", "list_scenario", "dict_materials"])
def test_historical_snapshot_domain_shape_cannot_be_resealed(db, malformation):
    created = create_company_profile(
        db, _write(), actor_id="admin-1", change_reason="第一版",
    )
    changed = CompanyProfileUpdate(
        profile={"name": "严格历史快照企业（二版）"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=1, change_reason="第二版",
    )
    update_company_profile(
        db, created.id, changed, expected_revision=1,
        actor_id="admin-2", change_reason="第二版",
    )
    first, second = db.query(AdminCompanyProfileAudit).order_by(
        AdminCompanyProfileAudit.revision,
    ).all()
    damaged = copy.deepcopy(first.after_snapshot)
    if malformation == "string_revision":
        damaged["revision"] = "1"
    elif malformation == "list_scenario":
        damaged["scenario"] = []
    else:
        damaged["materials"] = {}
    content = {
        key: damaged[key]
        for key in ("profile", "scenario", "scenario_data", "field_sources", "materials")
    }
    digest = hashlib.sha256(json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    damaged["content_sha256"] = digest
    first.after_snapshot = damaged
    first.content_sha256 = digest
    second.before_snapshot = copy.deepcopy(damaged)
    db.commit()

    with pytest.raises(AdminCompanyProfileIntegrityError, match="审计"):
        validate_company_profile_audit_history(db, created.id)


def test_duplicate_credit_code_update_is_409_and_rolls_back(db):
    admin_client = _router_client(
        db, SimpleNamespace(id="admin-1", is_active=True, is_superuser=True),
    )
    first = admin_client.post("/company-profiles", json={
        "profile": {"name": "信用代码企业甲", "credit_code": "CREDIT-CODE-A"},
        "change_reason": "创建甲",
    })
    second = admin_client.post("/company-profiles", json={
        "profile": {"name": "信用代码企业乙", "credit_code": "CREDIT-CODE-B"},
        "change_reason": "创建乙",
    })
    assert first.status_code == second.status_code == 201

    rejected = admin_client.put(f"/company-profiles/{second.json()['id']}", json={
        "profile": {"name": "信用代码企业乙", "credit_code": "CREDIT-CODE-A"},
        "expected_revision": 1,
        "change_reason": "错误占用甲代码",
    })
    assert rejected.status_code == 409
    db.expire_all()
    persisted = get_company_profile(db, second.json()["id"])
    assert persisted.credit_code == "CREDIT-CODE-B"
    assert persisted.revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 2


def test_missing_audit_or_current_row_drift_blocks_profile_snapshot(db):
    missing = create_company_profile(
        db, _write(name="审计缺失企业"), actor_id="admin-1", change_reason="创建",
    )
    db.query(AdminCompanyProfileAudit).filter(
        AdminCompanyProfileAudit.profile_id == str(missing.id),
    ).delete(synchronize_session=False)
    db.commit()
    with pytest.raises(AdminCompanyProfileValidationError, match="修订数量不连续"):
        get_active_profile_snapshot(db, missing.id)

    drifted = create_company_profile(
        db, _write(name="关系列漂移企业"), actor_id="admin-1", change_reason="创建",
    )
    drifted.name = "被直接改写的关系列"
    db.commit()
    with pytest.raises(AdminCompanyProfileValidationError, match="当前版本"):
        get_active_profile_snapshot(db, drifted.id)

    unattributed = create_company_profile(
        db, _write(name="归属漂移企业"), actor_id="admin-1", change_reason="创建",
    )
    unattributed.updated_by = "admin-forged"
    db.commit()
    with pytest.raises(AdminCompanyProfileIntegrityError, match="更新人"):
        validate_company_profile_audit_history(db, unattributed.id)

    archived = create_company_profile(
        db, _write(name="归档元数据企业"), actor_id="admin-1", change_reason="创建",
    )
    archive_company_profile(
        db, archived.id, expected_revision=1,
        actor_id="admin-2", change_reason="归档",
    )
    archived.archived_at = None
    db.commit()
    with pytest.raises(AdminCompanyProfileIntegrityError, match="归档时间"):
        validate_company_profile_audit_history(db, archived.id)


def test_audit_failure_rolls_back_profile_update(db, monkeypatch):
    import service.admin_company_profile_service as service_module

    created = create_company_profile(
        db, _write(), actor_id="admin-1", change_reason="初始版本",
    )
    changed = CompanyProfileUpdate(
        profile={"name": "不应落盘的更新"}, scenario="", scenario_data={},
        field_sources=[], materials=[], expected_revision=1, change_reason="模拟审计故障",
    )

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(service_module, "_append_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        update_company_profile(
            db, created.id, changed, expected_revision=1,
            actor_id="admin-2", change_reason="模拟审计故障",
        )
    db.expire_all()
    persisted = get_company_profile(db, created.id)
    assert persisted.name == "测试企业有限公司"
    assert persisted.revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 1


def test_response_models_reject_unknown_nested_fields():
    source = {
        **_trusted_registration_source("accounts_receivable_gross"),
        "sha256": None,
    }
    material = {
        "material_id": "material-1",
        "source_type": "company_submitted",
        "title": "应收账款台账",
        "content": "应收账款余额 1200 万元。",
        "reference": "台账 2026-08",
        "as_of_date": "2026-08-20",
        "date_unknown_reason": None,
        "eligible_for_structured_evidence": False,
    }
    assert FieldSourceResponse.model_validate(source).model_dump() == source
    assert MaterialResponse.model_validate(material).model_dump() == material
    with pytest.raises(ValidationError, match="internal_secret"):
        FieldSourceResponse.model_validate({**source, "internal_secret": "leak"})
    with pytest.raises(ValidationError, match="internal_secret"):
        MaterialResponse.model_validate({**material, "internal_secret": "leak"})
    with pytest.raises(ValidationError, match="eligible_for_structured_evidence"):
        MaterialResponse.model_validate({
            **material, "eligible_for_structured_evidence": True,
        })


def test_real_http_access_matrix_and_actor_redaction(db):
    normal = SimpleNamespace(id="user-1", is_active=True, is_superuser=False)
    admin = SimpleNamespace(id="admin-1", is_active=True, is_superuser=True)
    normal_client = _router_client(db, normal)
    admin_client = _router_client(db, admin)

    source = _trusted_registration_source("accounts_receivable_gross")
    material = {
        "material_id": "matrix-material-1",
        "source_type": "company_submitted",
        "title": "保理应收账款台账",
        "content": "截至报告日，应收账款账面余额为 1200 万元。",
        "reference": "内部台账 2026-08-20",
        "as_of_date": "2026-08-20",
    }
    draft = {
        "profile": {"name": "权限矩阵测试企业"},
        "scenario": "factoring",
        "scenario_data": {"accounts_receivable_gross": 1200},
        "field_sources": [source],
        "materials": [material],
        "change_reason": "权限矩阵创建",
    }
    valid_update = {
        **draft,
        "profile": {"name": "权限矩阵测试企业（更新）"},
        "expected_revision": 1,
        "change_reason": "权限矩阵更新",
    }

    created_response = admin_client.post("/company-profiles", json=draft)
    assert created_response.status_code == 201
    created = created_response.json()
    profile_id = created["id"]
    assert created["created_by"] == "admin-1"
    assert created["updated_by"] == "admin-1"

    anonymous_client = _anonymous_router_client(db)
    anonymous_requests = [
        anonymous_client.get("/company-profiles/templates"),
        anonymous_client.get("/company-profiles"),
        anonymous_client.get(f"/company-profiles/{profile_id}"),
        anonymous_client.post(
            f"/company-profiles/{profile_id}/materials/search",
            json={"query": "应收账款"},
        ),
        anonymous_client.post("/company-profiles", json=draft),
        anonymous_client.put(
            f"/company-profiles/{profile_id}", json=valid_update,
        ),
        anonymous_client.post(
            f"/company-profiles/{profile_id}/archive",
            json={"expected_revision": 1, "change_reason": "匿名归档"},
        ),
        anonymous_client.get(f"/company-profiles/{profile_id}/history"),
    ]
    assert {response.status_code for response in anonymous_requests} == {401}

    snapshot_before_reads = get_active_profile_snapshot(db, profile_id)
    assert normal_client.get("/company-profiles/templates").status_code == 200
    normal_list = normal_client.get("/company-profiles").json()
    assert normal_list["total"] == 1
    assert "updated_by" not in normal_list["items"][0]

    normal_detail_response = normal_client.get(f"/company-profiles/{profile_id}")
    assert normal_detail_response.status_code == 200
    normal_detail = normal_detail_response.json()
    assert {"created_by", "updated_by", "archived_by"}.isdisjoint(normal_detail)
    assert set(normal_detail["field_sources"][0]) == {
        "source_id", "name", "issuer", "source_type", "field_ids",
        "retrieved_at", "as_of_date", "reference", "sha256",
    }
    assert set(normal_detail["materials"][0]) == {
        "material_id", "source_type", "title", "content", "reference",
        "as_of_date", "date_unknown_reason", "eligible_for_structured_evidence",
    }
    assert normal_detail["materials"][0]["content"] == material["content"]

    material_search = normal_client.post(
        f"/company-profiles/{profile_id}/materials/search",
        json={"query": "应收账款 台账"},
    )
    assert material_search.status_code == 200
    assert material_search.json()["items"] == normal_detail["materials"]
    assert get_active_profile_snapshot(db, profile_id) == snapshot_before_reads

    assert normal_client.post("/company-profiles", json=draft).status_code == 403
    assert normal_client.put(
        f"/company-profiles/{profile_id}", json=valid_update,
    ).status_code == 403
    assert normal_client.post(
        f"/company-profiles/{profile_id}/archive",
        json={"expected_revision": 1, "change_reason": "越权归档"},
    ).status_code == 403
    assert normal_client.get(
        f"/company-profiles/{profile_id}/history",
    ).status_code == 403
    persisted = get_company_profile(db, profile_id)
    assert persisted.revision == 1
    assert db.query(AdminCompanyProfileAudit).count() == 1

    updated_response = admin_client.put(
        f"/company-profiles/{profile_id}", json=valid_update,
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["updated_by"] == "admin-1"
    history_response = admin_client.get(f"/company-profiles/{profile_id}/history")
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 2

    archived_response = admin_client.post(
        f"/company-profiles/{profile_id}/archive",
        json={"expected_revision": 2, "change_reason": "权限矩阵归档"},
    )
    assert archived_response.status_code == 200
    assert archived_response.json()["archived_by"] == "admin-1"

    hidden_list = normal_client.get("/company-profiles?include_archived=true")
    assert hidden_list.status_code == 200
    assert hidden_list.json()["total"] == 0
    assert normal_client.get(f"/company-profiles/{profile_id}").status_code == 404
    assert normal_client.post(
        f"/company-profiles/{profile_id}/materials/search",
        json={"query": "应收账款"},
    ).status_code == 404

    admin_list = admin_client.get("/company-profiles?include_archived=true").json()
    assert admin_list["total"] == 1
    assert admin_list["items"][0]["updated_by"] == "admin-1"
    archived_detail = admin_client.get(f"/company-profiles/{profile_id}")
    assert archived_detail.status_code == 200
    assert archived_detail.json()["archived_by"] == "admin-1"
    archived_search = admin_client.post(
        f"/company-profiles/{profile_id}/materials/search",
        json={"query": "应收账款"},
    )
    assert archived_search.status_code == 200
    assert archived_search.json()["items"] == archived_detail.json()["materials"]
    assert admin_client.get(
        f"/company-profiles/{profile_id}/history",
    ).json()["total"] == 3


def test_router_rbac_and_sqlite_crud_flow(db):
    app = FastAPI()
    app.include_router(router)
    normal = SimpleNamespace(id="user-1", is_superuser=False)
    admin = SimpleNamespace(id="admin-1", is_superuser=True)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_required] = lambda: admin
    client = TestClient(app)

    template_response = client.get("/company-profiles/templates")
    assert template_response.status_code == 200
    template = template_response.json()
    canonical_keys = {
        "field_id", "field_name", "category", "required", "description", "scope",
        "input_type",
    }
    assert template["version"] == "company-profile-template-v1"
    assert all(set(field) == canonical_keys for field in template["core"])
    assert all(
        field["scope"] == "scenario:factoring"
        for field in template["scenarios"]["factoring"]
    )

    source = _trusted_registration_source("accounts_receivable_gross")
    material = {
        "material_id": "api-material-1",
        "source_type": "company_submitted",
        "title": "保理应收账款台账",
        "content": "截至报告日，应收账款账面余额为 1200 万元。",
        "reference": "内部台账 2026-08-20",
        "as_of_date": "2026-08-20",
    }
    draft = {
        "profile": {"name": "API 保理企业"},
        "scenario": "factoring",
        # Scenario data is keyed directly by field ID, never nested under the
        # selected scenario name.
        "scenario_data": {"accounts_receivable_gross": 1200},
        "field_sources": [source],
        "materials": [material],
        "change_reason": "HTTP 契约创建",
    }
    create_response = client.post("/company-profiles", json=draft)
    assert create_response.status_code == 201
    created = create_response.json()
    profile_id = created["id"]
    assert created["scenario_data"] == {"accounts_receivable_gross": 1200}
    assert created["field_sources"] == [{**source, "sha256": None}]
    assert created["materials"] == [{
        **material,
        "date_unknown_reason": None,
        "eligible_for_structured_evidence": False,
    }]

    fetched = client.get(f"/company-profiles/{profile_id}")
    assert fetched.status_code == 200
    assert fetched.json()["scenario_data"] == {"accounts_receivable_gross": 1200}
    assert fetched.json()["field_sources"] == created["field_sources"]
    assert fetched.json()["materials"] == created["materials"]

    material_search = client.post(
        f"/company-profiles/{profile_id}/materials/search", json={"query": "应收账款 台账"},
    )
    assert material_search.status_code == 200
    assert set(material_search.json()) == {"items", "total"}
    assert material_search.json()["total"] == 1
    assert material_search.json()["items"] == created["materials"]

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
        **draft,
        "profile": {"name": "API 保理企业（更正）"},
        "expected_revision": 1,
        "change_reason": "HTTP 契约更新",
    })
    assert first_update.status_code == 200
    conflict = client.put(f"/company-profiles/{profile_id}", json={
        "profile": {"name": "API 企业 3"}, "expected_revision": 1, "change_reason": "陈旧",
    })
    assert conflict.status_code == 409

    history = client.get(f"/company-profiles/{profile_id}/history")
    assert history.status_code == 200
    assert set(history.json()) == {"items", "total"}
    assert history.json()["total"] == 2
    assert [item["revision"] for item in history.json()["items"]] == [2, 1]

    missing_archive_revision = client.post(
        f"/company-profiles/{profile_id}/archive", json={"change_reason": "遗漏版本号"},
    )
    assert missing_archive_revision.status_code == 422
    stale_archive = client.post(
        f"/company-profiles/{profile_id}/archive",
        json={"expected_revision": 1, "change_reason": "陈旧版本"},
    )
    assert stale_archive.status_code == 409
    archived = client.post(
        f"/company-profiles/{profile_id}/archive",
        json={"expected_revision": 2, "change_reason": "归档测试"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["revision"] == 3
