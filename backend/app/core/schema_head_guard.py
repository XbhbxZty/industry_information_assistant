"""Read-only runtime guard for the repository's Alembic schema head.

Importing this module deliberately neither imports Alembic/SQLAlchemy nor opens a
database connection.  The application and maintenance scripts call the guard only
at their runtime boundary, after migrations have been run explicitly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class SchemaHeadGuardError(RuntimeError):
    """The connected database is not exactly at this checkout's Alembic head."""


def _default_alembic_ini() -> Path:
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def get_alembic_heads(alembic_ini: str | Path | None = None) -> tuple[str, ...]:
    """Resolve all heads from this checkout's Alembic revision graph.

    Alembic is imported only while resolving the graph, so importing the guard is
    safe for migration tooling and other configuration-only callers.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(alembic_ini) if alembic_ini is not None else _default_alembic_ini()))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def _version_mismatch_message(expected: tuple[str, ...], found: tuple[str, ...]) -> str:
    expected_display = ", ".join(expected)
    found_display = ", ".join(found) if found else "none"
    return (
        "Database schema is not at this checkout's Alembic head "
        f"(expected: {expected_display}; found: {found_display}). "
        "Run `python -m alembic upgrade head` from backend/ before starting the service. "
        "This read-only guard will not migrate or stamp the database."
    )


def _read_version_rows(connection: Any) -> tuple[str, ...]:
    # SQLAlchemy stays a runtime-only import just like Alembic above.
    from sqlalchemy import text

    try:
        rows = connection.execute(
            text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        ).scalars().all()
    except Exception as error:
        raise SchemaHeadGuardError(
            "Cannot read public.alembic_version. Run `python -m alembic upgrade head` "
            "from backend/ before starting the service. This read-only guard will not "
            "migrate or stamp the database."
        ) from error

    if not all(isinstance(version, str) for version in rows):
        raise SchemaHeadGuardError(
            "public.alembic_version contains an invalid revision value; run "
            "`python -m alembic upgrade head` from backend/. This read-only guard will "
            "not migrate or stamp the database."
        )
    return tuple(rows)


def assert_database_schema_at_head(
    bind: Any, *, alembic_ini: str | Path | None = None,
) -> None:
    """Fail closed unless *bind* reports exactly this checkout's Alembic heads.

    ``bind`` may be a SQLAlchemy Engine or an already-open Connection.  The only
    database operation is a ``SELECT`` against Alembic's version table; this
    function never invokes Alembic commands, DDL, or ``stamp``.
    """
    expected = get_alembic_heads(alembic_ini)
    if not expected:
        raise SchemaHeadGuardError("The repository has no Alembic head to validate.")

    if hasattr(bind, "connect"):
        with bind.connect() as connection:
            found = _read_version_rows(connection)
    else:
        found = _read_version_rows(bind)

    if found != expected:
        raise SchemaHeadGuardError(_version_mismatch_message(expected, found))
