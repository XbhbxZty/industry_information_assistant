"""Small real-PG adoption matrix; reuse the identity-checked disposable DB fixture."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Event

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import RootTransaction
from alembic import command
from alembic.config import Config

from test_legacy_schema_preflight_postgres import (
    disposable_postgres_database,  # noqa: F401
    _create_application_schema,
    _execute_psycopg_sql,
    _public_relation_names,
)
from core.legacy_schema_adoption import (
    CONFIRMATION_PHRASE, load_adoption_approval, load_trusted_target_policy,
    serialize_adoption_approval, serialize_trusted_target_policy,
    trusted_target_policy_sha256,
)
from core import legacy_schema_adoption_executor as executor
from scripts import adopt_legacy_schema as cli
from core.schema_head_guard import assert_database_schema_at_head, SchemaHeadGuardError


pytestmark = pytest.mark.postgres_integration


def _inputs(target):
    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            templates = cli.inspect_adoption(connection)
    finally:
        engine.dispose()
    templates["approval_template"].update(
        operator_reference="test-operator",
        operator_attested_backup_reference="test-disposable-database",
        operator_attested_maintenance_window_reference="test-isolated-window",
        confirmation_phrase=CONFIRMATION_PHRASE,
    )
    return (
        load_adoption_approval(json.dumps(templates["approval_template"])),
        load_trusted_target_policy(json.dumps(templates["policy_template"])),
    )


def _apply(target, approval, policy):
    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            return executor.adopt_legacy_schema(connection, approval, policy)
    finally:
        engine.dispose()


def test_adoption_preserves_data_uses_public_version_and_is_idempotent(disposable_postgres_database):
    target = disposable_postgres_database
    _create_application_schema(target)
    _execute_psycopg_sql(target, """
        INSERT INTO public.users (id, username, email, hashed_password)
        VALUES ('11111111-1111-4111-8111-111111111111', 'preserved', 'test@example.invalid', 'dummy')
    """)
    approval, policy = _inputs(target)
    engine = create_engine(target.sqlalchemy_url, connect_args={"options": "-c search_path=pg_catalog"})
    try:
        with engine.connect() as connection:
            assert executor.adopt_legacy_schema(connection, approval, policy).status == "adopted"
            assert not connection.in_transaction()
            assert connection.exec_driver_sql("SELECT username FROM public.users").scalar_one() == "preserved"
            assert connection.exec_driver_sql(
                "SELECT version_num FROM public.alembic_version"
            ).scalar_one() == executor.HEAD_REVISION
    finally:
        engine.dispose()
    assert _apply(target, approval, policy).status == "already_managed"


@pytest.mark.parametrize("statement", [
    "ALTER TABLE public.users ALTER COLUMN is_active SET DEFAULT false",
    "CREATE TABLE public.unexpected (id integer)",
])
def test_drift_after_inspection_never_stamps(disposable_postgres_database, statement):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)
    _execute_psycopg_sql(target, statement)
    result = _apply(target, approval, policy)
    assert result.status == "rejected"
    assert result.error_code == "preflight_mismatch"
    assert "alembic_version" not in _public_relation_names(target)


@pytest.mark.parametrize("case", ["target", "digest", "expired"])
def test_bad_target_digest_or_expired_approval_never_stamps(disposable_postgres_database, case):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)
    if case == "target":
        policy = replace(policy, database_identity_sha256="f" * 64)
        approval = replace(approval, expected_target_policy_sha256=trusted_target_policy_sha256(policy))
    elif case == "digest":
        approval = replace(approval, expected_preflight_sha256="f" * 64)
    else:
        from datetime import datetime
        confirmed = datetime.strptime(approval.confirmed_at, "%Y-%m-%dT%H:%M:%S.%fZ")
        approval = replace(
            approval,
            confirmed_at=(confirmed - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            expires_at=(confirmed - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
    assert _apply(target, approval, policy).status == "rejected"
    assert "alembic_version" not in _public_relation_names(target)


def test_post_stamp_failure_rolls_back_version_table(disposable_postgres_database, monkeypatch):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)

    def fail_after_stamp(connection):
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()
        raise RuntimeError("injected postverify failure")

    monkeypatch.setattr(executor, "_verify_stamp", fail_after_stamp)
    result = _apply(target, approval, policy)
    assert result.status == "rejected"
    assert "alembic_version" not in _public_relation_names(target)


def test_commit_ack_failure_reports_unknown_without_retry(disposable_postgres_database, monkeypatch):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)
    real_commit = RootTransaction.commit

    def lose_commit_ack(transaction):
        real_commit(transaction)
        raise ConnectionError("simulated lost commit acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(RootTransaction, "commit", lose_commit_ack)
        result = _apply(target, approval, policy)
    assert result.status == "outcome_unknown"
    assert result.error_code == "commit_outcome_unknown"
    assert _apply(target, approval, policy).status == "already_managed"


def test_competing_adopter_is_busy_and_only_one_stamp_occurs(disposable_postgres_database, monkeypatch):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)
    entered, release = Event(), Event()
    actual_stamp = executor._stamp
    calls = []

    def pause_stamp(connection):
        calls.append(connection)
        entered.set()
        assert release.wait(timeout=10)
        actual_stamp(connection)

    monkeypatch.setattr(executor, "_stamp", pause_stamp)
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(_apply, target, approval, policy)
        try:
            assert entered.wait(timeout=10)
            second = _apply(target, approval, policy)
            assert second.status == "rejected"
            assert second.error_code == "adoption_busy"
        finally:
            release.set()
        assert first.result(timeout=10).status == "adopted"
    assert len(calls) == 1


def test_cli_loads_host_policy_and_records_outcome(disposable_postgres_database, monkeypatch, tmp_path, capsys):
    target = disposable_postgres_database
    _create_application_schema(target)
    approval, policy = _inputs(target)
    policy_file, approval_file = tmp_path / "policy.json", tmp_path / "approval.json"
    policy_file.write_text(serialize_trusted_target_policy(policy), encoding="utf-8")
    approval_file.write_text(serialize_adoption_approval(approval), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", target.sqlalchemy_url)
    monkeypatch.setenv("LEGACY_ADOPTION_POLICY_FILE", str(policy_file))
    capsys.readouterr()
    assert cli.main(["apply", "--approval", str(approval_file)]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "adopted"
    assert result["approval_id"] == approval.approval_id
    assert target.sqlalchemy_url not in output.out + output.err


def test_real_head_guard_rejects_unmigrated_stale_and_accepts_upgrade(disposable_postgres_database):
    target = disposable_postgres_database
    engine = create_engine(target.sqlalchemy_url)
    config = Config(str(executor.BACKEND / "alembic.ini"))
    config.attributes["database_url"] = target.sqlalchemy_url
    try:
        with pytest.raises(SchemaHeadGuardError):
            assert_database_schema_at_head(engine)
        assert _public_relation_names(target) == set()
        command.upgrade(config, "head")
        assert_database_schema_at_head(engine)
        command.check(config)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("UPDATE public.alembic_version SET version_num = 'outdated'")
                with pytest.raises(SchemaHeadGuardError):
                    assert_database_schema_at_head(connection)
            finally:
                transaction.rollback()
        assert_database_schema_at_head(engine)
    finally:
        engine.dispose()
