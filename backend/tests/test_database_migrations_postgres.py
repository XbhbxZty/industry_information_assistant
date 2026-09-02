"""Real-PostgreSQL lifecycle test for the empty-database baseline."""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import psycopg2
from psycopg2 import sql
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database_url import resolve_database_urls  # noqa: E402
from test_database_migration_contract import EXPECTED_APPLICATION_TABLES  # noqa: E402


DATABASE_PREFIX = "codex_migration_test_"
SAFE_DATABASE_NAME = re.compile(rf"^{DATABASE_PREFIX}[0-9a-f]{{32}}$")


def _alembic_config(target_url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = target_url
    return config


@pytest.fixture
def empty_postgres_database():
    admin_url = os.getenv("MIGRATION_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set MIGRATION_TEST_ADMIN_URL to run real PostgreSQL migration tests")

    resolved_admin = resolve_database_urls({"DATABASE_URL": admin_url})
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex}"
    assert SAFE_DATABASE_NAME.fullmatch(database_name)

    admin_connection = psycopg2.connect(resolved_admin.psycopg_conninfo)
    admin_connection.autocommit = True
    try:
        with admin_connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    finally:
        admin_connection.close()

    target_url = make_url(resolved_admin.sqlalchemy_url).set(database=database_name)
    rendered_target_url = target_url.render_as_string(hide_password=False)
    try:
        yield rendered_target_url
    finally:
        if not SAFE_DATABASE_NAME.fullmatch(database_name):
            raise RuntimeError("refusing to drop an unrecognized migration test database")
        cleanup_connection = psycopg2.connect(resolved_admin.psycopg_conninfo)
        cleanup_connection.autocommit = True
        try:
            with cleanup_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        finally:
            cleanup_connection.close()


@pytest.mark.postgres_integration
def test_empty_database_upgrade_check_downgrade_and_reupgrade(empty_postgres_database):
    target_url = empty_postgres_database
    config = _alembic_config(target_url)

    command.upgrade(config, "head")
    engine = create_engine(target_url)
    try:
        tables_after_upgrade = set(inspect(engine).get_table_names())
        assert tables_after_upgrade == EXPECTED_APPLICATION_TABLES | {"alembic_version"}
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "20260902_0002"
    finally:
        engine.dispose()

    # This is the authoritative metadata-vs-database drift check.
    command.check(config)

    command.downgrade(config, "base")
    engine = create_engine(target_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    command.check(config)
