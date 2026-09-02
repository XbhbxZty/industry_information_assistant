"""D2b integration tests; only identity-checked disposable PostgreSQL databases."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime

from alembic import command
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_legacy_schema_preflight_postgres import (  # noqa: F401
    _alembic_config, disposable_postgres_database,
)
from test_admin_company_profiles import _write
from models.company_profile import (
    AdminCompanyProfile, AdminCompanyProfileAudit, AdminCompanyProfileAuditAnchor,
)
from service import admin_company_profile_service as service
from service.company_profile_audit_backfill import backfill_company_profile_audit_anchors


pytestmark = pytest.mark.postgres_integration
BASELINE = "20260831_0001"
HEAD = "20260902_0002"


def _insert_legacy(connection, *, name="legacy profile", broken=False, revisions=2, profile_id=None):
    """Use only reflected 0001 tables; never create legacy data via new signing code."""
    metadata = MetaData()
    profiles = Table("admin_company_profiles", metadata, autoload_with=connection)
    audits = Table("admin_company_profile_audits", metadata, autoload_with=connection)
    profile_id = profile_id or str(uuid.uuid4())
    now = datetime(2026, 8, 20, 10, 0)
    before = None
    for revision in range(1, revisions + 1):
        values = service.prepare_company_profile_content(_write(name=f"{name} v{revision}"))
        after = {"id": profile_id, "status": "active", "revision": revision, **values}
        if broken and revision == revisions:
            after["content_sha256"] = "0" * 64
        connection.execute(audits.insert().values(
            id=str(uuid.uuid4()), profile_id=profile_id, revision=revision,
            action="created" if revision == 1 else "updated", actor_id="legacy-admin",
            change_reason=f"legacy revision {revision}", before_snapshot=before,
            after_snapshot=after, content_sha256=after["content_sha256"], created_at=now,
        ))
        before = copy.deepcopy(after)
    connection.execute(profiles.insert().values(
        id=profile_id, status="active", revision=revisions, name=values["name"],
        credit_code=values["credit_code"], content_sha256=after["content_sha256"],
        profile_json=values["profile"], scenario=values["scenario"],
        scenario_data=values["scenario_data"], field_sources=values["field_sources"],
        materials=values["materials"], created_by="legacy-admin", updated_by="legacy-admin",
        created_at=now, updated_at=now,
    ))
    return profile_id


def _legacy_rows(connection, profile_id):
    return connection.execute(text(
        "SELECT id, profile_id, revision, action, actor_id, change_reason, "
        "before_snapshot, after_snapshot, content_sha256, created_at "
        "FROM public.admin_company_profile_audits WHERE profile_id = :id ORDER BY revision"
    ), {"id": profile_id}).mappings().all()


def test_upgrade_anchors_untouched_legacy_and_services_extend_chain(disposable_postgres_database, audit_signing_key):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, BASELINE)
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            profile_id = _insert_legacy(connection)
            original = _legacy_rows(connection, profile_id)
        command.upgrade(config, HEAD)
        command.check(config)
        with engine.connect() as connection:
            assert _legacy_rows(connection, profile_id) == original
        with Session(engine, expire_on_commit=False) as db:
            row = service.get_company_profile(db, profile_id)
            anchor = db.get(AdminCompanyProfileAuditAnchor, profile_id)
            assert anchor.legacy_cutover_revision == 2
            assert row.audit_head_mac == anchor.anchor_mac
            assert all(service._audit_is_legacy(item) for item in service.list_company_profile_history(db, profile_id))
            original_anchor = service._anchor_record(anchor)
            service.update_company_profile(
                db, profile_id, _write(name="signed third version"), expected_revision=2,
                actor_id="admin", change_reason="after migration",
            )
            service.archive_company_profile(db, profile_id, expected_revision=3, actor_id="admin", change_reason="archive")
            audits = service.validate_company_profile_audit_history(db, profile_id)
            assert audits[2].previous_audit_mac == anchor.anchor_mac
            assert [a.chain_start for a in audits[2:]] == ["legacy_anchor", "legacy_anchor"]
            assert audits[-1].audit_mac == row.audit_head_mac
        with engine.begin() as connection:
            assert backfill_company_profile_audit_anchors(connection, migration_run_id="retry") == 0
        with Session(engine) as db:
            assert service._anchor_record(db.get(AdminCompanyProfileAuditAnchor, profile_id)) == original_anchor
            assert service.get_company_profile(db, profile_id, include_archived=True).revision == 4
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["broken_history", "missing_key", "orphan_audit"])
def test_legacy_failure_rolls_back_ddl_anchor_and_version(disposable_postgres_database, audit_signing_key, monkeypatch, failure):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, BASELINE)
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            # Force valid-before-broken so a failure occurs after a first anchor
            # has already been flushed, proving whole-upgrade rollback.
            _insert_legacy(connection, name="valid profile", profile_id="00000000-0000-4000-8000-000000000001")
            profile_id = _insert_legacy(connection, name="invalid profile", broken=failure == "broken_history", profile_id="00000000-0000-4000-8000-000000000002")
            if failure == "orphan_audit":
                connection.execute(text("DELETE FROM public.admin_company_profiles WHERE id = :id"), {"id": profile_id})
            original = _legacy_rows(connection, profile_id)
        if failure == "missing_key":
            monkeypatch.delenv("COMPANY_PROFILE_AUDIT_KEYS_JSON")
            monkeypatch.delenv("COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID")
        expected = service.AdminCompanyProfileUnavailable if failure == "missing_key" else service.AdminCompanyProfileIntegrityError
        with pytest.raises(expected):
            command.upgrade(config, HEAD)
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == BASELINE
            assert "admin_company_profile_audit_anchors" not in inspect(connection).get_table_names()
            assert "audit_mac" not in {c["name"] for c in inspect(connection).get_columns("admin_company_profile_audits")}
            assert "audit_head_mac" not in {c["name"] for c in inspect(connection).get_columns("admin_company_profiles")}
            assert _legacy_rows(connection, profile_id) == original
    finally:
        engine.dispose()


def test_postgres_rejects_history_mutation_and_supports_native_signed_crud(disposable_postgres_database, audit_signing_key):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, BASELINE)
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with engine.begin() as connection:
            legacy_id = _insert_legacy(connection, revisions=1)
        command.upgrade(config, HEAD)
        with Session(engine) as db:
            native = service.create_company_profile(db, _write(name="native"), actor_id="admin", change_reason="create")
            native_id = native.id
            service.update_company_profile(db, native_id, _write(name="native v2"), expected_revision=1, actor_id="admin", change_reason="update")
            assert [a.chain_start for a in service.validate_company_profile_audit_history(db, native_id)] == ["genesis", "genesis"]
        for table, column in [("admin_company_profile_audits", "change_reason"), ("admin_company_profile_audit_anchors", "migration_run_id")]:
            for statement in [f"UPDATE public.{table} SET {column} = 'forged'", f"DELETE FROM public.{table}", f"TRUNCATE public.{table}"]:
                with pytest.raises(DBAPIError):
                    with engine.begin() as connection:
                        connection.execute(text(statement))
        with Session(engine) as db:
            assert len(service.validate_company_profile_audit_history(db, legacy_id)) == 1
            assert len(service.validate_company_profile_audit_history(db, native_id)) == 2
        # An operator bypassing triggers is outside ordinary application rights,
        # but even such an edit must fail verification without the signing key.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE public.admin_company_profile_audit_anchors DISABLE TRIGGER USER"))
            connection.execute(text("UPDATE public.admin_company_profile_audit_anchors SET migration_run_id = 'forged'"))
            connection.execute(text("ALTER TABLE public.admin_company_profile_audit_anchors ENABLE TRIGGER USER"))
        with Session(engine) as db:
            with pytest.raises(service.AdminCompanyProfileIntegrityError):
                service.get_company_profile(db, legacy_id)
    finally:
        engine.dispose()


def test_postgres_final_verification_failure_rolls_back_actual_rows(disposable_postgres_database, audit_signing_key, monkeypatch):
    config = _alembic_config(disposable_postgres_database)
    command.upgrade(config, HEAD)
    engine = create_engine(disposable_postgres_database.sqlalchemy_url)
    try:
        with Session(engine) as db:
            row = service.create_company_profile(db, _write(), actor_id="admin", change_reason="create")
            profile_id, original_head = row.id, row.audit_head_mac
            real_verify = service.verify_observed_audit_chain

            def reject_second(observations, **kwargs):
                if kwargs["expected_head"].revision == 2:
                    raise service.CompanyProfileAuditIntegrityError("injected failure")
                return real_verify(observations, **kwargs)

            monkeypatch.setattr(service, "verify_observed_audit_chain", reject_second)
            with pytest.raises(service.AdminCompanyProfileIntegrityError):
                service.update_company_profile(db, profile_id, _write(name="must rollback"), expected_revision=1, actor_id="admin", change_reason="update")
        with Session(engine) as db:
            restored = service.get_company_profile(db, profile_id)
            assert restored.revision == 1 and restored.audit_head_mac == original_head
            assert db.query(AdminCompanyProfileAudit).filter_by(profile_id=profile_id).count() == 1
    finally:
        engine.dispose()
