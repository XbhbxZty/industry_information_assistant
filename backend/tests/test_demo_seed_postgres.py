"""Opt-in PostgreSQL contracts for the explicit Text2SQL demo seed command."""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import create_engine, text


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.schema_head_guard import SchemaHeadGuardError  # noqa: E402
from scripts import seed_demo_data  # noqa: E402
from test_legacy_schema_preflight_postgres import (  # noqa: E402
    _alembic_config,
    _public_relation_names,
    disposable_postgres_database,
)


@pytest.mark.postgres_integration
def test_demo_seed_requires_head_then_creates_once_without_migration_drift(
    disposable_postgres_database,
):
    target = disposable_postgres_database
    engine = create_engine(target.sqlalchemy_url, connect_args={"options": "-c search_path=pg_catalog"})
    try:
        with pytest.raises(SchemaHeadGuardError, match="upgrade head"):
            seed_demo_data.seed_demo_data(engine)
        assert _public_relation_names(target) == set()

        command.upgrade(_alembic_config(target), "head")
        seed_demo_data.seed_demo_data(engine)

        assert set(seed_demo_data.DEMO_TABLES) <= _public_relation_names(target)
        with engine.connect() as connection:
            restaurant_count = connection.execute(text("SELECT count(*) FROM public.restaurants")).scalar_one()
            stock_count = connection.execute(text("SELECT count(*) FROM public.stocks")).scalar_one()
        assert restaurant_count == 10
        assert stock_count == 10

        # These unmanaged tables must not cause Alembic autogenerate/check drift.
        command.check(_alembic_config(target))

        with pytest.raises(seed_demo_data.DemoSeedError, match="existing demo table"):
            seed_demo_data.seed_demo_data(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM public.restaurants")).scalar_one() == 10
            assert connection.execute(text("SELECT count(*) FROM public.stocks")).scalar_one() == 10
    finally:
        engine.dispose()


def test_demo_resource_contains_only_the_seven_optional_tables():
    resource = seed_demo_data.DEMO_SQL_RESOURCE.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" not in resource
    assert "CREATE EXTENSION" not in resource
    assert "CREATE TRIGGER" not in resource
    assert "update_updated_at_column" not in resource
    for table in seed_demo_data.DEMO_TABLES:
        assert f"CREATE TABLE {table}" in resource
    for managed_table in ("users", "chat_sessions", "documents", "long_term_memories"):
        assert f"CREATE TABLE {managed_table}" not in resource


def test_demo_cli_loads_dotenv_before_resolving_target(monkeypatch):
    from types import SimpleNamespace
    import dotenv
    import sqlalchemy

    calls = []
    engine = SimpleNamespace(dispose=lambda: calls.append("dispose"))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda path: calls.append(("dotenv", path)))

    def resolve():
        assert calls == [("dotenv", BACKEND / ".env")]
        calls.append("resolve")
        return SimpleNamespace(sqlalchemy_url="test-only")

    monkeypatch.setattr(seed_demo_data, "resolve_database_urls", resolve)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(seed_demo_data, "seed_demo_data", lambda bind: calls.append("seed"))
    assert seed_demo_data.main() == 0
    assert calls[1:] == ["resolve", "seed", "dispose"]
