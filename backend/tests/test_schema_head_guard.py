"""Unit contracts for the read-only Alembic-head startup guard."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import schema_head_guard  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, rows=None, error=None):
        self.rows = rows
        self.error = error
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.error is not None:
            raise self.error
        return _Result(self.rows)


class _Engine:
    def __init__(self, connection):
        self.connection = connection
        self.connected = False

    def connect(self):
        self.connected = True
        return self

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_guard_import_does_not_import_sqlalchemy_or_alembic_or_connect():
    code = (
        f"import sys; sys.path.insert(0, {str(APP)!r}); import core.schema_head_guard; "
        "assert 'sqlalchemy' not in sys.modules; assert 'alembic' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_guard_resolves_the_checkout_alembic_head_dynamically():
    expected = tuple(sorted(ScriptDirectory.from_config(
        Config(str(BACKEND / "alembic.ini"))
    ).get_heads()))
    assert schema_head_guard.get_alembic_heads() == expected


def test_guard_accepts_exact_version_rows_and_uses_only_a_select(monkeypatch):
    monkeypatch.setattr(schema_head_guard, "get_alembic_heads", lambda _=None: ("head-a",))
    connection = _Connection(rows=["head-a"])
    engine = _Engine(connection)

    schema_head_guard.assert_database_schema_at_head(engine)

    assert engine.connected is True
    assert len(connection.statements) == 1
    assert "SELECT version_num FROM public.alembic_version" in connection.statements[0]


@pytest.mark.parametrize("found", [[], ["old-head"], ["head-a", "head-a"]])
def test_guard_rejects_missing_stale_or_duplicate_version_rows(monkeypatch, found):
    monkeypatch.setattr(schema_head_guard, "get_alembic_heads", lambda _=None: ("head-a",))

    with pytest.raises(schema_head_guard.SchemaHeadGuardError, match="upgrade head"):
        schema_head_guard.assert_database_schema_at_head(_Connection(rows=found))


def test_guard_reports_unreadable_version_table_without_migrating(monkeypatch):
    monkeypatch.setattr(schema_head_guard, "get_alembic_heads", lambda _=None: ("head-a",))

    with pytest.raises(schema_head_guard.SchemaHeadGuardError, match="read-only guard"):
        schema_head_guard.assert_database_schema_at_head(_Connection(error=RuntimeError("no table")))


def test_runtime_and_seed_paths_do_not_call_create_all_and_use_the_guard():
    sources = {
        "app_main.py": APP / "app_main.py",
        "init_industry_data.py": APP / "scripts" / "init_industry_data.py",
        "seed_industry_data.py": APP / "scripts" / "seed_industry_data.py",
    }
    for name, path in sources.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_all"
            for node in ast.walk(tree)
        ), name
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "assert_database_schema_at_head"
            for node in ast.walk(tree)
        ), name


def test_start_paths_upgrade_explicitly_and_never_mount_legacy_business_ddl():
    root = BACKEND.parent
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    start_app = (root / "start-app.ps1").read_text(encoding="utf-8")
    start_services = (root / "start-services.sh").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "python -m alembic -c /app/alembic.ini upgrade head && exec uvicorn" in dockerfile
    assert "alembic stamp" not in dockerfile
    assert start_app.index("python -m alembic upgrade head") < start_app.index("Start-Process")
    assert start_app.count("-WindowStyle Hidden -FilePath") == 2
    assert "python -m alembic upgrade head" in start_services
    assert "docker-entrypoint-initdb.d" not in compose
    assert ".env" in (BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()
