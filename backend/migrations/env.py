"""Alembic environment for the application-owned PostgreSQL schema."""
from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
import sys
from typing import Any

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection


config = context.config

if config.config_file_name is not None:
    # Library callers may already have application/security loggers loaded.
    # A migration must not silently disable those loggers for the process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
for import_path in (str(BACKEND_DIR), str(APP_DIR)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

# Match application configuration without importing FastAPI startup.
load_dotenv(BACKEND_DIR / ".env")

import models  # noqa: E402,F401  # register every mapped table on Base.metadata
from core.database import Base  # noqa: E402
from core.database_url import resolve_database_urls  # noqa: E402
from core.legacy_schema_preflight import DOCKER_DEMO_RELATIONS, LANGGRAPH_RELATIONS  # noqa: E402


target_metadata = Base.metadata


def _include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    # With include_schemas=False PostgreSQL reflects public as schema=None,
    # while the explicitly qualified version table uses schema='public'.
    # Exclude that exact name plus the known non-ORM packages, never prefixes.
    external = DOCKER_DEMO_RELATIONS | LANGGRAPH_RELATIONS | {"alembic_version"}
    return type_ != "table" or name not in external


def _database_url() -> str:
    """Resolve the target without copying credentials into alembic.ini."""
    override = config.attributes.get("database_url")
    if override is not None:
        if not isinstance(override, str):
            raise TypeError("Alembic database_url attribute must be a string")
        return resolve_database_urls({"DATABASE_URL": override}).sqlalchemy_url
    return resolve_database_urls().sqlalchemy_url


def _configure(connection: Connection, **extra: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        version_table_schema="public",
        include_name=_include_name,
        **extra,
    )


def run_migrations_offline() -> None:
    """Emit SQL without opening a database connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run with either a caller-owned connection or a short-lived engine."""
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("Alembic connection attribute must be a SQLAlchemy Connection")
        _configure(supplied_connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
