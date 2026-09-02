"""Import/override contracts for the isolated process acceptance entrypoint.

These checks intentionally never provide a disposable DB URL and never start
Uvicorn.  The real PostgreSQL acceptance suite owns that integration boundary.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
TESTS = BACKEND / "tests"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)


def test_entry_refuses_unisolated_environment_before_importing_the_app():
    """A bare import cannot load dotenv/app code or contact a database."""
    env = {"PYTHONPATH": os.pathsep.join((str(TESTS), str(APP)))}
    result = subprocess.run(
        [sys.executable, "-c", "import e2e_app"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert "e2e_app requires PYTHON_DOTENV_DISABLED=1" in (result.stdout + result.stderr)
    assert "app_main" not in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "url",
    (
        "postgresql://tester@localhost:5432/postgres",
        "postgresql+psycopg2://tester@127.0.0.1:5432/postgres",
        "postgresql://tester@[::1]:5432/postgres",
    ),
)
def test_process_acceptance_allows_only_plain_local_admin_urls(url):
    from test_process_acceptance_postgres import _validate_local_admin_url

    _validate_local_admin_url(url)


@pytest.mark.parametrize(
    "url",
    (
        "postgresql://tester@db.example.test:5432/postgres",
        "postgresql://tester@127.0.0.1:5432/postgres?host=db.example.test",
        "postgresql://tester@localhost:5432/postgres?service=production",
    ),
)
def test_process_acceptance_rejects_remote_and_libpq_query_overrides(url):
    from test_process_acceptance_postgres import _validate_local_admin_url

    with pytest.raises(ValueError, match="local PostgreSQL URL"):
        _validate_local_admin_url(url)


@pytest.mark.parametrize("query", ("host=db.example.test", "service=production"))
def test_entry_rejects_libpq_query_overrides_before_app_import(query):
    env = {
        "PYTHONPATH": os.pathsep.join((str(TESTS), str(APP))),
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": (
            "postgresql://tester@127.0.0.1:5432/"
            f"codex_d2a2_preflight_{'a' * 32}?{query}"
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import e2e_app"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode != 0
    assert "e2e_app refuses DATABASE_URL query overrides" in (result.stdout + result.stderr)


def test_v1_dependency_is_captured_from_the_research_router_module():
    """Protect against confusing router package exports with the module itself."""
    package = importlib.import_module("router")
    module = importlib.import_module("router.research_router")

    assert package.research_router is not module
    assert callable(module.get_research_service)
    stream = next(route for route in module.router.routes if route.name == "stream_research")
    assert any(dependency.call is module.get_research_service for dependency in stream.dependant.dependencies)

    source = (TESTS / "e2e_app.py").read_text(encoding="utf-8")
    assert 'importlib.import_module("router.research_router")' in source
    assert "app_main.app.dependency_overrides[_original_get_research_service]" in source


def test_scout_constructor_is_resolved_from_the_graph_module_global():
    """The entry must patch this symbol before constructing its service."""
    graph = importlib.import_module("service.deep_research_v2.graph")

    assert graph.DeepResearchGraph.__init__.__globals__["DeepScout"] is graph.DeepScout
    source = (TESTS / "e2e_app.py").read_text(encoding="utf-8")
    assert "graph_module.DeepScout = _NoNetworkScout" in source
