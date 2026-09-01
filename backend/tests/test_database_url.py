"""Pure regression coverage for the single PostgreSQL URL authority."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database_url import DatabaseUrlConfigurationError, resolve_database_urls  # noqa: E402


def test_database_url_takes_priority_and_normalizes_both_driver_forms():
    urls = resolve_database_urls({
        "DATABASE_URL": "postgresql+psycopg://reader:secret@db.example.test:5544/due?sslmode=require",
        "POSTGRES_HOST": "must-not-be-used",
        "POSTGRES_PORT": "not-a-port",
    })
    assert urls.source == "DATABASE_URL"
    assert urls.sqlalchemy_url == (
        "postgresql+psycopg2://reader:secret@db.example.test:5544/due?sslmode=require"
    )
    assert urls.psycopg_conninfo == (
        "postgresql://reader:secret@db.example.test:5544/due?sslmode=require"
    )


def test_component_values_are_percent_encoded_for_both_consumers():
    urls = resolve_database_urls({
        "POSTGRES_USER": "reader@corp",
        "POSTGRES_PASSWORD": "p: /?@#",
        "POSTGRES_HOST": "db.example.test",
        "POSTGRES_PORT": "5433",
        "POSTGRES_DB": "due diligence",
    })
    suffix = "reader%40corp:p%3A%20%2F%3F%40%23@db.example.test:5433/due%20diligence"
    assert urls.sqlalchemy_url == f"postgresql+psycopg2://{suffix}"
    assert urls.psycopg_conninfo == f"postgresql://{suffix}"


def test_explicit_url_percent_encoding_round_trips_to_canonical_forms():
    urls = resolve_database_urls({
        "DATABASE_URL": "postgres://reader%40corp:p%3A%20%2F%3F%40%23@[::1]:5432/due%20diligence",
    })
    suffix = "reader%40corp:p%3A%20%2F%3F%40%23@[::1]:5432/due%20diligence"
    assert urls.sqlalchemy_url == f"postgresql+psycopg2://{suffix}"
    assert urls.psycopg_conninfo == f"postgresql://{suffix}"


def test_empty_postgresql_password_is_preserved_for_trust_authentication():
    urls = resolve_database_urls({
        "POSTGRES_USER": "reader",
        "POSTGRES_PASSWORD": "",
        "POSTGRES_HOST": "db.example.test",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "due",
    })
    assert urls.sqlalchemy_url == "postgresql+psycopg2://reader:@db.example.test:5432/due"
    assert urls.psycopg_conninfo == "postgresql://reader:@db.example.test:5432/due"


def test_explicit_url_without_port_uses_postgresql_default():
    urls = resolve_database_urls({
        "DATABASE_URL": "postgresql://reader:secret@db.example.test/due",
    })
    assert urls.sqlalchemy_url == (
        "postgresql+psycopg2://reader:secret@db.example.test:5432/due"
    )
    assert urls.psycopg_conninfo == (
        "postgresql://reader:secret@db.example.test:5432/due"
    )


@pytest.mark.parametrize("database_url", [
    "",
    "   ",
    "mysql://user:password@db.example.test:3306/due",
    "postgresql://user:password@db.example.test:0/due",
    "postgresql://user:password@/due",
    "postgresql://user:password@db.example.test:5432/",
    "postgresql://user:password@db.example.test:5432/due#fragment",
    "postgresql+asyncpg://user:password@db.example.test:5432/due",
])
def test_invalid_database_url_fails_closed(database_url):
    with pytest.raises(DatabaseUrlConfigurationError):
        resolve_database_urls({"DATABASE_URL": database_url})


def test_non_string_database_url_fails_closed():
    with pytest.raises(DatabaseUrlConfigurationError):
        resolve_database_urls({"DATABASE_URL": 5432})  # type: ignore[arg-type]


def test_present_but_blank_database_url_never_falls_back_to_components():
    with pytest.raises(DatabaseUrlConfigurationError):
        resolve_database_urls({
            "DATABASE_URL": " ",
            "POSTGRES_HOST": "fallback.example.test",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "reader",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "due",
        })


@pytest.mark.parametrize("components", [
    {"POSTGRES_PORT": "not-a-port"},
    {"POSTGRES_PORT": "65536"},
    {"POSTGRES_HOST": "db.example.test/path"},
])
def test_invalid_component_configuration_fails_closed(components):
    with pytest.raises(DatabaseUrlConfigurationError):
        resolve_database_urls(components)
