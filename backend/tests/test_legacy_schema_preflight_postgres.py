"""Real PostgreSQL contracts for the read-only legacy-schema preflight.

This suite is intentionally opt-in.  It creates only databases with the
``codex_d2a2_preflight_`` prefix and records their OID, owner, and PostgreSQL
cluster identity before every destructive cleanup step.  A changed target is
left in place and fails loudly rather than risking a broad or misdirected drop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import sys
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
import psycopg2
from psycopg2 import sql
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database_url import resolve_database_urls  # noqa: E402
from core.legacy_schema_preflight import (  # noqa: E402
    APPLICATION_TABLES,
    HEAD_REVISION,
    LANGGRAPH_MANIFEST_ID,
    LANGGRAPH_PROVIDER_MIGRATIONS,
    PreflightStatus,
    preflight_legacy_schema,
)
from scripts import preflight_legacy_schema as preflight_cli  # noqa: E402


DATABASE_PREFIX = "codex_d2a2_preflight_"
SAFE_DATABASE_NAME = re.compile(rf"^{DATABASE_PREFIX}[0-9a-f]{{32}}$")


@dataclass(frozen=True)
class _DatabaseTarget:
    name: str
    oid: int
    owner: str
    cluster_identity: tuple[object, ...]
    sqlalchemy_url: str = field(repr=False)
    psycopg_conninfo: str = field(repr=False)


def _cluster_identity(cursor) -> tuple[object, ...]:
    """Return a stable enough identity to prevent cross-cluster cleanup."""
    cursor.execute(
        """
        SELECT (pg_control_system()).system_identifier::text,
               pg_catalog.host(pg_catalog.inet_server_addr()),
               pg_catalog.inet_server_port(),
               current_setting('server_version_num'),
               current_setting('cluster_name', true),
               pg_postmaster_start_time()::text
        """
    )
    value = cursor.fetchone()
    if value is None:
        raise RuntimeError("PostgreSQL cluster identity query returned no row")
    return tuple(value)


def _database_identity(cursor, database_name: str) -> tuple[str, int, str] | None:
    cursor.execute(
        """
        SELECT datname, oid::bigint, pg_get_userbyid(datdba)
        FROM pg_catalog.pg_database
        WHERE datname = %s
        """,
        (database_name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return (row[0], int(row[1]), row[2])


def _assert_cleanup_target(cursor, target: _DatabaseTarget) -> None:
    if _cluster_identity(cursor) != target.cluster_identity:
        raise RuntimeError(
            "refusing cleanup: PostgreSQL cluster/server identity changed; "
            f"left {target.name} in place"
        )
    identity = _database_identity(cursor, target.name)
    expected = (target.name, target.oid, target.owner)
    if identity != expected:
        raise RuntimeError(
            "refusing cleanup: database name/OID/owner no longer match; "
            f"expected {expected!r}, observed {identity!r}; left target in place"
        )


def _cleanup_target(admin_conninfo: str, target: _DatabaseTarget) -> None:
    """Terminate/drop only after two independent target identity checks."""
    connection = psycopg2.connect(admin_conninfo)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _assert_cleanup_target(cursor, target)
            cursor.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_catalog.pg_stat_activity
                WHERE datid = %s AND pid <> pg_backend_pid()
                """,
                (target.oid,),
            )
            # Re-read both identities after terminating backends to close the
            # only practical time-of-check/time-of-use window before DROP.
            _assert_cleanup_target(cursor, target)
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target.name)))
    finally:
        connection.close()


@pytest.fixture
def disposable_postgres_database():
    admin_url = os.getenv("MIGRATION_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set MIGRATION_TEST_ADMIN_URL to run real PostgreSQL preflight tests")

    resolved_admin = resolve_database_urls({"DATABASE_URL": admin_url})
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
    assert SAFE_DATABASE_NAME.fullmatch(database_name)
    admin_connection = psycopg2.connect(resolved_admin.psycopg_conninfo)
    admin_connection.autocommit = True
    target: _DatabaseTarget | None = None
    try:
        with admin_connection.cursor() as cursor:
            cluster_identity = _cluster_identity(cursor)
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
            identity = _database_identity(cursor, database_name)
            if identity is None:
                raise RuntimeError("created PostgreSQL test database cannot be identified")
        target_sqlalchemy_url = make_url(resolved_admin.sqlalchemy_url).set(
            database=database_name,
        )
        target_conninfo = target_sqlalchemy_url.set(
            drivername="postgresql",
        ).render_as_string(hide_password=False)
        target = _DatabaseTarget(
            name=identity[0],
            oid=identity[1],
            owner=identity[2],
            cluster_identity=cluster_identity,
            sqlalchemy_url=target_sqlalchemy_url.render_as_string(hide_password=False),
            psycopg_conninfo=target_conninfo,
        )
        yield target
    finally:
        admin_connection.close()
        if target is not None:
            _cleanup_target(resolved_admin.psycopg_conninfo, target)


def _preflight(target: _DatabaseTarget):
    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            return preflight_legacy_schema(connection)
    finally:
        engine.dispose()


def _public_relation_names(target: _DatabaseTarget) -> set[str]:
    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT c.relname
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
                """
            ).scalars()
            return set(rows.all())
    finally:
        engine.dispose()


def _create_application_schema(target: _DatabaseTarget) -> None:
    import models  # noqa: F401
    from core.database import Base

    assert set(Base.metadata.tables) == APPLICATION_TABLES
    engine = create_engine(target.sqlalchemy_url)
    try:
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()


def _execute_psycopg_sql(target: _DatabaseTarget, statement: str) -> None:
    connection = psycopg2.connect(target.psycopg_conninfo)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
    finally:
        connection.close()


def _alembic_config(target: _DatabaseTarget) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = target.sqlalchemy_url
    return config


@pytest.mark.postgres_integration
def test_empty_database_requires_upgrade_without_any_stamp_or_write(disposable_postgres_database):
    target = disposable_postgres_database
    before = _public_relation_names(target)

    report = _preflight(target)

    assert report.status is PreflightStatus.UPGRADE_REQUIRED
    assert report.differences == ()
    assert _public_relation_names(target) == before == set()
    assert "alembic_version" not in _public_relation_names(target)


@pytest.mark.postgres_integration
def test_base_create_all_is_exact_adoptable_without_alembic_version(disposable_postgres_database):
    target = disposable_postgres_database
    _create_application_schema(target)
    before = _public_relation_names(target)

    report = _preflight(target)

    assert report.status is PreflightStatus.EXACT_ADOPTABLE
    assert report.differences == ()
    assert _public_relation_names(target) == before
    assert "alembic_version" not in before


@pytest.mark.postgres_integration
@pytest.mark.parametrize("create_remaining_application_tables", [False, True])
def test_docker_legacy_variants_are_known_incompatible_and_never_stamped(
    disposable_postgres_database,
    create_remaining_application_tables,
):
    target = disposable_postgres_database
    _execute_psycopg_sql(target, (BACKEND.parent / "docker" / "init-db" / "01-init.sql").read_text(encoding="utf-8"))
    if create_remaining_application_tables:
        _create_application_schema(target)
    before = _public_relation_names(target)

    report = _preflight(target)

    assert report.status is PreflightStatus.KNOWN_INCOMPATIBLE
    assert report.profile_id == (
        "docker_hybrid_v1" if create_remaining_application_tables else "docker_only_v1"
    )
    assert report.differences == ()
    assert _public_relation_names(target) == before
    assert "alembic_version" not in before


@pytest.mark.postgres_integration
def test_langgraph_312_exact_four_table_package_is_unmanaged(disposable_postgres_database):
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    target = disposable_postgres_database
    pool = ConnectionPool(
        target.psycopg_conninfo,
        min_size=1,
        max_size=1,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    try:
        PostgresSaver(pool).setup()
    finally:
        pool.close()

    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            migrations = tuple(connection.exec_driver_sql(
                "SELECT v FROM public.checkpoint_migrations ORDER BY v"
            ).scalars().all())
    finally:
        engine.dispose()
    assert migrations == LANGGRAPH_PROVIDER_MIGRATIONS
    assert _public_relation_names(target) == {
        "checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes",
    }

    report = _preflight(target)

    assert report.status is PreflightStatus.UPGRADE_REQUIRED
    assert report.differences == ()
    assert report.unmanaged_packages == (LANGGRAPH_MANIFEST_ID,)


@pytest.mark.postgres_integration
@pytest.mark.parametrize(
    ("drift_name", "statement", "expected_path", "expected_issue"),
    [
        (
            "default",
            "ALTER TABLE public.users ALTER COLUMN is_active SET DEFAULT false",
            ".default_sql",
            "changed",
        ),
        (
            "type",
            "ALTER TABLE public.users ALTER COLUMN username TYPE varchar(51)",
            ".type_sql",
            "changed",
        ),
        (
            "trigger",
            """
            CREATE FUNCTION public.preflight_drift_touch() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
            CREATE TRIGGER preflight_drift_touch BEFORE UPDATE ON public.users
            FOR EACH ROW EXECUTE FUNCTION public.preflight_drift_touch();
            """,
            'triggers[["public","users","preflight_drift_touch"]]',
            "unexpected",
        ),
        (
            "index",
            "CREATE INDEX preflight_drift_users_active_idx ON public.users (is_active)",
            'indexes[["public","users","preflight_drift_users_active_idx"]]',
            "unexpected",
        ),
    ],
)
def test_single_component_drift_has_a_precise_catalog_difference(
    disposable_postgres_database,
    drift_name,
    statement,
    expected_path,
    expected_issue,
):
    target = disposable_postgres_database
    _create_application_schema(target)
    _execute_psycopg_sql(target, statement)

    report = _preflight(target)

    assert report.status is PreflightStatus.SCHEMA_DRIFT, drift_name
    assert any(
        difference.issue == expected_issue and expected_path in difference.path
        for difference in report.differences
    ), (drift_name, report.differences)
    assert "alembic_version" not in _public_relation_names(target)


@pytest.mark.postgres_integration
def test_unknown_and_partial_allowlist_relations_report_the_exact_difference(
    disposable_postgres_database,
):
    from langgraph.checkpoint.postgres import PostgresSaver

    target = disposable_postgres_database
    _execute_psycopg_sql(target, PostgresSaver.MIGRATIONS[0])
    _execute_psycopg_sql(target, "CREATE TABLE public.preflight_unknown_relation (id integer)")

    report = _preflight(target)

    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        difference.path == "relations"
        and difference.issue == "unexpected_relation"
        and difference.actual == ["preflight_unknown_relation"]
        for difference in report.differences
    )
    assert any(
        difference.path == "manifest.unmanaged_langgraph_postgres_3_1_2.relations"
        and difference.issue == "missing_package_relations"
        and difference.actual == ["checkpoint_migrations"]
        for difference in report.differences
    )


@pytest.mark.postgres_integration
def test_real_rls_policy_and_event_trigger_are_observed_and_rejected(
    disposable_postgres_database,
):
    target = disposable_postgres_database
    _create_application_schema(target)
    _execute_psycopg_sql(
        target,
        """
        ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
        CREATE POLICY preflight_users_reader ON public.users
        FOR SELECT TO PUBLIC USING (true);
        CREATE FUNCTION public.preflight_guard_ddl() RETURNS event_trigger
        LANGUAGE plpgsql AS $$ BEGIN RETURN; END; $$;
        CREATE EVENT TRIGGER preflight_guard_ddl ON ddl_command_start
        EXECUTE FUNCTION public.preflight_guard_ddl();
        """,
    )

    report = _preflight(target)

    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        difference.path.endswith(".row_security")
        and difference.issue == "changed"
        and difference.actual is True
        for difference in report.differences
    )
    assert any(
        'policies[["public","users","preflight_users_reader"]]' in difference.path
        and difference.issue == "unexpected"
        for difference in report.differences
    )
    assert any(
        difference.path == "event_triggers"
        and difference.issue == "unexpected_event_trigger"
        for difference in report.differences
    )
    assert "alembic_version" not in _public_relation_names(target)


@pytest.mark.postgres_integration
def test_real_rule_acl_types_default_acl_and_routine_dependency_are_rejected(
    disposable_postgres_database,
):
    target = disposable_postgres_database
    _create_application_schema(target)
    _execute_psycopg_sql(
        target,
        """
        CREATE RULE preflight_block_user_insert AS ON INSERT TO public.users
        DO INSTEAD NOTHING;
        GRANT SELECT ON public.documents TO PUBLIC;
        CREATE TYPE public.preflight_risk_level AS ENUM ('low', 'high');
        CREATE DOMAIN public.preflight_positive_integer AS integer CHECK (VALUE > 0);
        ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC;
        CREATE FUNCTION public.preflight_read_user_ids() RETURNS SETOF uuid
        LANGUAGE SQL
        BEGIN ATOMIC
          SELECT id FROM public.users;
        END;
        """,
    )

    report = _preflight(target)

    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        'rules[["public","users","preflight_block_user_insert"]]' in item.path
        for item in report.differences
    )
    assert any(item.path.startswith("relation_privileges[") for item in report.differences)
    assert any(item.issue == "unexpected_standalone_type" for item in report.differences)
    assert any(item.issue == "unexpected_default_privilege" for item in report.differences)
    assert any(item.issue == "unexpected_dependency" for item in report.differences)
    assert "alembic_version" not in _public_relation_names(target)


@pytest.mark.postgres_integration
def test_real_managed_unmanaged_fk_and_trigger_edges_are_explicitly_rejected(
    disposable_postgres_database,
):
    target = disposable_postgres_database
    _execute_psycopg_sql(
        target,
        (BACKEND.parent / "docker" / "init-db" / "01-init.sql").read_text(
            encoding="utf-8"
        ),
    )
    _create_application_schema(target)
    _execute_psycopg_sql(
        target,
        """
        ALTER TABLE public.restaurants ADD COLUMN linked_user_id uuid;
        ALTER TABLE public.restaurants ADD CONSTRAINT restaurants_user_fk
        FOREIGN KEY (linked_user_id) REFERENCES public.users(id);
        CREATE TRIGGER restaurant_managed_touch BEFORE UPDATE ON public.restaurants
        FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
        """,
    )

    report = _preflight(target)

    assert report.status is PreflightStatus.SCHEMA_DRIFT
    assert any(
        item.path == "cross_boundary.constraint.restaurants_user_fk"
        and item.issue == "managed_unmanaged_dependency"
        for item in report.differences
    )
    assert any(
        item.path == "cross_boundary.trigger.restaurant_managed_touch"
        and item.issue == "managed_unmanaged_dependency"
        for item in report.differences
    )


@pytest.mark.postgres_integration
def test_real_restricted_role_gets_stable_permission_error_without_secret(
    disposable_postgres_database,
    monkeypatch,
    capsys,
):
    target = disposable_postgres_database
    command.upgrade(_alembic_config(target), "head")
    capsys.readouterr()
    role_name = f"codex_d2a2_reader_{uuid.uuid4().hex}"
    role_pattern = re.compile(r"^codex_d2a2_reader_[0-9a-f]{32}$")
    assert role_pattern.fullmatch(role_name)
    role_password = uuid.uuid4().hex
    admin = psycopg2.connect(target.psycopg_conninfo)
    admin.autocommit = True
    role_oid: int | None = None
    role_created = False
    try:
        with admin.cursor() as cursor:
            assert _cluster_identity(cursor) == target.cluster_identity
            cursor.execute(
                "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname = %s",
                (role_name,),
            )
            if cursor.fetchone() is not None:
                raise RuntimeError("refusing role creation: random role name already exists")
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(role_password),
            ))
            role_created = True
            cursor.execute(
                "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname = %s",
                (role_name,),
            )
            role_oid = int(cursor.fetchone()[0])
        restricted_url = make_url(target.sqlalchemy_url).set(
            username=role_name,
            password=role_password,
        ).render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", restricted_url)

        assert preflight_cli.main([]) == 3
        captured = capsys.readouterr()
        assert restricted_url not in captured.err
        payload = json.loads(captured.err)
        assert payload["status"] == "preflight_error"
        assert payload["error_code"] == "legacy_preflight_permission_denied"
    finally:
        try:
            with admin.cursor() as cursor:
                if _cluster_identity(cursor) != target.cluster_identity:
                    raise RuntimeError("refusing role cleanup: PostgreSQL cluster changed")
                if role_created:
                    cursor.execute(
                        "SELECT oid::bigint, rolname FROM pg_catalog.pg_roles WHERE rolname = %s",
                        (role_name,),
                    )
                    observed = cursor.fetchone()
                    if role_oid is None or observed != (role_oid, role_name):
                        raise RuntimeError(
                            "refusing role cleanup: role name/OID changed; left role in place"
                        )
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity "
                        "WHERE usesysid = %s AND pid <> pg_backend_pid()",
                        (role_oid,),
                    )
                    cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role_name)))
        finally:
            admin.close()


@pytest.mark.postgres_integration
def test_alembic_head_is_already_managed(disposable_postgres_database):
    target = disposable_postgres_database
    command.upgrade(_alembic_config(target), "head")

    report = _preflight(target)

    assert report.status is PreflightStatus.ALREADY_MANAGED
    assert report.differences == ()
    assert _public_relation_names(target) == APPLICATION_TABLES | {"alembic_version"}
    engine = create_engine(target.sqlalchemy_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM public.alembic_version"
            ).scalar_one() == HEAD_REVISION
    finally:
        engine.dispose()
