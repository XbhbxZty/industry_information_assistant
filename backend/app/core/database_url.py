"""One import-safe authority for PostgreSQL connection strings.

This module deliberately does not create an engine or import application models.
Migration tooling, synchronous SQLAlchemy callers, and psycopg3 consumers can
therefore resolve the same database target without importing the FastAPI app.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit


class DatabaseUrlConfigurationError(ValueError):
    """Database connection configuration is malformed or uses an unsafe scheme."""


@dataclass(frozen=True)
class DatabaseConnectionUrls:
    """Equivalent URLs for SQLAlchemy's sync driver and psycopg3."""

    sqlalchemy_url: str
    psycopg_conninfo: str
    source: str


_POSTGRES_SCHEMES = frozenset({
    "postgres",
    "postgresql",
    "postgresql+psycopg2",
    "postgresql+psycopg",
})


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DatabaseUrlConfigurationError(f"{label} must be a non-blank string")
    if any(ord(character) < 32 for character in value):
        raise DatabaseUrlConfigurationError(f"{label} contains a control character")
    return value


def _secret(value: object, label: str) -> str:
    """Accept an empty password for PostgreSQL trust/peer configurations."""
    if not isinstance(value, str):
        raise DatabaseUrlConfigurationError(f"{label} must be a string")
    if any(ord(character) < 32 for character in value):
        raise DatabaseUrlConfigurationError(f"{label} contains a control character")
    return value


def _port(value: object, label: str) -> int:
    text = _nonempty(value, label)
    if not text.isascii() or not text.isdecimal():
        raise DatabaseUrlConfigurationError(f"{label} must be an integer port")
    port = int(text)
    if not 1 <= port <= 65535:
        raise DatabaseUrlConfigurationError(f"{label} is outside the valid port range")
    return port


def _url_host(host: str) -> str:
    # urlsplit().hostname removes IPv6 brackets. Restore them for a URL while
    # retaining ordinary DNS names exactly as a host component (not a path).
    if ":" in host:
        return f"[{host}]"
    return host


def _render_urls(*, user: str, password: str, host: str, port: int, database: str,
                 query: str, source: str) -> DatabaseConnectionUrls:
    user = _nonempty(user, "PostgreSQL user")
    password = _secret(password, "PostgreSQL password")
    host = _nonempty(host, "PostgreSQL host")
    database = _nonempty(database, "PostgreSQL database")
    if any(character in host for character in "/@?#"):
        raise DatabaseUrlConfigurationError("PostgreSQL host contains URL delimiters")

    credentials = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    location = f"{credentials}{_url_host(host)}:{port}/{quote(database, safe='')}"
    suffix = f"?{query}" if query else ""
    return DatabaseConnectionUrls(
        sqlalchemy_url=f"postgresql+psycopg2://{location}{suffix}",
        psycopg_conninfo=f"postgresql://{location}{suffix}",
        source=source,
    )


def _from_database_url(raw_url: str) -> DatabaseConnectionUrls:
    value = _nonempty(raw_url, "DATABASE_URL")
    parsed = urlsplit(value)
    if parsed.scheme not in _POSTGRES_SCHEMES:
        raise DatabaseUrlConfigurationError("DATABASE_URL must use a PostgreSQL URL scheme")
    if parsed.fragment:
        raise DatabaseUrlConfigurationError("DATABASE_URL must not contain a fragment")
    if parsed.hostname is None:
        raise DatabaseUrlConfigurationError("DATABASE_URL must include a host")
    try:
        parsed_port: Optional[int] = parsed.port
    except ValueError as exc:
        raise DatabaseUrlConfigurationError("DATABASE_URL contains an invalid port") from exc
    if parsed_port is None:
        parsed_port = 5432
    elif not 1 <= parsed_port <= 65535:
        raise DatabaseUrlConfigurationError("DATABASE_URL must include a valid port")
    if parsed.username is None:
        raise DatabaseUrlConfigurationError("DATABASE_URL must include a user")
    if not parsed.path or parsed.path == "/":
        raise DatabaseUrlConfigurationError("DATABASE_URL must include a database name")
    database = unquote(parsed.path[1:])
    if "/" in database:
        raise DatabaseUrlConfigurationError("DATABASE_URL database name must be one path segment")
    return _render_urls(
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        host=parsed.hostname,
        port=parsed_port,
        database=database,
        query=parsed.query,
        source="DATABASE_URL",
    )


def resolve_database_urls(environ: Optional[Mapping[str, str]] = None) -> DatabaseConnectionUrls:
    """Resolve one PostgreSQL target, with ``DATABASE_URL`` taking precedence.

    Component variables remain supported for existing local deployments. They are
    percent-encoded before becoming URLs, avoiding ambiguity when a password has
    characters such as ``@``, ``:``, ``/`` or ``?``.
    """
    values: Mapping[str, str] = os.environ if environ is None else environ
    if "DATABASE_URL" in values:
        raw_url = values["DATABASE_URL"]
        if not isinstance(raw_url, str):
            raise DatabaseUrlConfigurationError("DATABASE_URL must be a string")
        return _from_database_url(raw_url)

    def component(name: str, default: str) -> str:
        value = values.get(name, default)
        if name == "POSTGRES_PASSWORD":
            return _secret(value, name)
        return _nonempty(value, name)

    return _render_urls(
        user=component("POSTGRES_USER", "postgres"),
        password=component("POSTGRES_PASSWORD", "postgres123"),
        host=component("POSTGRES_HOST", "localhost"),
        port=_port(component("POSTGRES_PORT", "5432"), "POSTGRES_PORT"),
        database=component("POSTGRES_DB", "industry_assistant"),
        query="",
        source="POSTGRES_*",
    )
