"""Authorization and relation-scope regression tests for database explorer."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (os.fspath(BACKEND), os.fspath(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.database import get_db  # noqa: E402
from router.auth_router import get_current_user_required  # noqa: E402
from router.database_router import router  # noqa: E402
from service.database_explorer import (  # noqa: E402
    DatabaseExplorer,
    require_allowed_database_table,
    validate_allowed_select,
)
from service.text2sql_service import Text2SQLService  # noqa: E402


def _client(user, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_required] = lambda: user
    monkeypatch.setattr(DatabaseExplorer, "get_tables", lambda self: [])
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/database/tables", None),
        ("get", "/database/tables/admin_company_profile_audits/data", None),
        ("post", "/database/query", {"sql": "SELECT * FROM users"}),
        ("post", "/database/text2sql", {"question": "列出用户"}),
    ],
)
def test_database_routes_reject_normal_users_before_data_access(
    method,
    path,
    json,
    monkeypatch,
):
    client = _client(
        SimpleNamespace(id="user-1", is_active=True, is_superuser=False),
        monkeypatch,
    )
    response = client.request(method, path, json=json)
    assert response.status_code == 403


def test_database_routes_allow_server_verified_superuser(monkeypatch):
    client = _client(
        SimpleNamespace(id="admin-1", is_active=True, is_superuser=True),
        monkeypatch,
    )
    response = client.get("/database/tables")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "table_name",
    ["users", "admin_company_profiles", "admin_company_profile_audits", "admin_company_profile_audit_anchors",
     "research_review_claims"],
)
def test_table_endpoints_reject_relations_outside_public_demo_scope(table_name):
    with pytest.raises(ValueError, match="not available"):
        require_allowed_database_table(table_name)
    with pytest.raises(ValueError, match="not available"):
        DatabaseExplorer(object()).get_table_data(table_name)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM industry_stats",
        "SELECT * FROM company_data WHERE industry = 'smart_transport'",
        "SELECT * FROM policy_data WHERE policy_name = 'A ''quoted'' policy'",
        "SELECT company_name, revenue FROM company_data WHERE year = 2024 LIMIT 10;",
        'SELECT policy_name FROM "policy_data" AS p ORDER BY publish_date DESC',
        "SELECT year, SUM(metric_value) FROM industry_stats GROUP BY year",
    ],
)
def test_limited_select_contract_accepts_the_three_demo_tables(sql):
    assert validate_allowed_select(sql)
    service = object.__new__(Text2SQLService)
    assert service.validate_sql(sql) == (True, "")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT * FROM admin_company_profile_audits",
        "SELECT * FROM admin_company_profile_audit_anchors",
        "SELECT * FROM research_review_claims",
        "SELECT * FROM public.company_data",
        "SELECT * FROM company_data, users",
        "SELECT * FROM company_data JOIN users ON true",
        "SELECT * FROM company_data WHERE id IN (SELECT id FROM users)",
        "WITH leaked AS (SELECT * FROM users) SELECT * FROM leaked",
        "SELECT * FROM company_data UNION SELECT * FROM users",
        "SELECT pg_read_file('/etc/passwd') FROM company_data",
        "SELECT pg_read_file(source) FROM industry_stats",
        "SELECT 动态查询(source) FROM industry_stats",
        'SELECT "pg_read_file"(source) FROM industry_stats',
        'SELECT "query_to_xml"("chr"(65), true, false, NULL) FROM policy_data',
        "SELECT current_user FROM company_data",
        "SELECT * FROM company_data WHERE EXISTS (TABLE users)",
    ],
)
def test_limited_select_contract_fails_closed_for_other_or_complex_relations(sql):
    with pytest.raises(ValueError):
        validate_allowed_select(sql)
    service = object.__new__(Text2SQLService)
    allowed, error = service.validate_sql(sql)
    assert allowed is False
    assert error
