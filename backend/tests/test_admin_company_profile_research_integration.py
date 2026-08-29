"""Stage-3 regression tests for explicit managed-profile research snapshots.

The CRUD feature is intentionally outside this test's scope.  A tiny fake
``admin_company_profile_service`` exercises the frozen hand-off contract at the
research boundary without requiring a live database.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

research_router = importlib.import_module("router.research_router")  # noqa: E402
from service import company_profile as profile_module  # noqa: E402
from service.deep_research_v2.graph import DeepResearchGraph  # noqa: E402
from service.deep_research_v2.service import DeepResearchV2Service  # noqa: E402
from service.deep_research_v2.state import create_initial_state  # noqa: E402


def _snapshot(profile: dict[str, Any], scenario: str = "factoring") -> dict[str, Any]:
    scenario_data = {
        "accounts_receivable_gross": 1250,
        # A core field in scenario data must never overwrite the core field's
        # regular mapping or policy.
        "revenue": 999999,
        "not_a_check": "must be ignored",
    }
    field_sources = [{
        "source_id": "managed-factoring-source",
        "name": "受权应收账款台账",
        "issuer": "管理端",
        "source_type": "authorized",
        "field_ids": ["accounts_receivable_gross"],
        "retrieved_at": "2026-08-20T00:00:00+00:00",
        "as_of_date": "2026-08-20",
        "reference": "managed://factoring-source",
        "sha256": None,
    }]
    content = json.dumps({
        "profile": profile, "scenario": scenario, "scenario_data": scenario_data,
        "field_sources": field_sources, "materials": [],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ref = {
        "id": "b5a9e5e3-56ca-4dbd-8942-997868b416a7",
        "revision": 7,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "source": "admin_company_profile",
    }
    decorated = copy.deepcopy(profile)
    decorated["_admin_profile_ref"] = copy.deepcopy(ref)
    decorated["_scenario_data"] = scenario_data
    decorated["_admin_field_sources"] = field_sources
    return {
        "profile": decorated,
        "ref": ref,
        "scenario": scenario,
    }


def _profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "company_id": "managed-co-1",
        "name": "管理端优先企业有限公司",
        "credit_code": "91000000TEST0001",
        "coverage": {"queried": [], "retrieved_at": "2026-08-20T00:00:00+00:00"},
        "registration": {},
        "financials": [],
        "judicial_records": [],
    }
    profile.update(overrides)
    return profile


def _install_profile_service(monkeypatch: pytest.MonkeyPatch, callback):
    module = types.ModuleType("service.admin_company_profile_service")

    class AdminCompanyProfileNotFound(Exception):
        pass

    class AdminCompanyProfileValidationError(Exception):
        pass

    module.AdminCompanyProfileNotFound = AdminCompanyProfileNotFound
    module.AdminCompanyProfileValidationError = AdminCompanyProfileValidationError
    module.get_active_profile_snapshot = callback
    monkeypatch.setitem(sys.modules, "service.admin_company_profile_service", module)
    return AdminCompanyProfileNotFound, AdminCompanyProfileValidationError


def _graph_without_adapters(monkeypatch: pytest.MonkeyPatch) -> DeepResearchGraph:
    # `_load_company_profile` needs no initialized agents.  Avoid configuration,
    # network clients, database checkpoints, and real data-source adapters.
    graph = object.__new__(DeepResearchGraph)
    import service.datasource as datasource

    monkeypatch.setattr(datasource, "apply_all", lambda company, checks, store: {})
    return graph


def _check(state: dict[str, Any], field_id: str) -> dict[str, Any]:
    return next(item for item in state["field_checks"] if item["field_id"] == field_id)


def test_router_reads_and_detaches_authorized_snapshot_before_streaming(monkeypatch: pytest.MonkeyPatch):
    source = _snapshot(_profile())
    requested: list[tuple[Any, str]] = []
    _install_profile_service(
        monkeypatch,
        lambda db, profile_id: requested.append((db, profile_id)) or source,
    )

    frozen = research_router._load_admin_company_profile_snapshot(object(), source["ref"]["id"])

    assert requested and requested[0][1] == source["ref"]["id"]
    assert frozen == source
    source["profile"]["_scenario_data"]["accounts_receivable_gross"] = 9
    assert frozen["profile"]["_scenario_data"]["accounts_receivable_gross"] == 1250


@pytest.mark.parametrize("kind, expected_status", [("missing", 404), ("invalid", 400)])
def test_router_fails_closed_for_missing_archived_or_invalid_snapshot(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected_status: int
):
    def raise_not_found(_db, _profile_id):
        raise not_found("missing or archived")

    def raise_invalid(_db, _profile_id):
        raise invalid("bad active payload")

    not_found, invalid = _install_profile_service(
        monkeypatch, raise_not_found if kind == "missing" else raise_invalid
    )

    with pytest.raises(HTTPException) as exc_info:
        research_router._load_admin_company_profile_snapshot(object(), "requested-id")
    assert exc_info.value.status_code == expected_status


def test_explicit_snapshot_wins_over_same_name_static_profile_and_maps_selected_scenario(
    monkeypatch: pytest.MonkeyPatch,
):
    snapshot = _snapshot(_profile())
    state = create_initial_state(
        "请对管理端优先企业有限公司开展尽调",
        "snapshot-run",
        due_diligence=True,
        provided_company_profile=snapshot["profile"],
        admin_profile_ref=snapshot["ref"],
        admin_profile_scenario=snapshot["scenario"],
    )
    graph = _graph_without_adapters(monkeypatch)
    called = False

    def forbidden_static_lookup(_query: str):
        nonlocal called
        called = True
        return {"name": "管理端优先企业有限公司"}

    monkeypatch.setattr(profile_module, "find_company", forbidden_static_lookup)
    company = graph._load_company_profile(state["query"], state)

    assert company and company["name"] == "管理端优先企业有限公司"
    assert not called, "显式 profile ID 的路径不得回退到静态名称匹配"
    assert state["subject_name"] == "管理端优先企业有限公司"
    assert state["admin_profile_ref"] == snapshot["ref"]
    scenario_check = _check(state, "accounts_receivable_gross")
    assert scenario_check["scope"] == "scenario:factoring"
    assert scenario_check["status"] == "verified"
    assert scenario_check["value"] == "1250"
    assert scenario_check["attempted_sources"] == ["admin_company_profile"]
    # Scenario decorations neither forge core facts nor alter immutable
    # ChecklistItem.required semantics.
    revenue = _check(state, "revenue")
    assert revenue["status"] == "unverified"
    assert revenue["required"] is True
    assert all("1250" not in fact["content"] for fact in state["facts"])


def test_state_and_graph_reject_mutated_external_snapshot_without_static_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    profile = _profile()
    snapshot = _snapshot(profile)
    state = create_initial_state(
        "尽调",
        "immutable-run",
        due_diligence=True,
        provided_company_profile=snapshot["profile"],
        admin_profile_ref=snapshot["ref"],
        admin_profile_scenario="factoring",
    )
    profile["name"] = "外部后来修改的企业"
    assert state["provided_company_profile"]["name"] == "管理端优先企业有限公司"

    graph = _graph_without_adapters(monkeypatch)
    monkeypatch.setattr(profile_module, "find_company", lambda _query: pytest.fail("不得名称回退"))
    state["provided_company_profile"]["name"] = "检查点被篡改"
    with pytest.raises(ValueError, match="快照校验失败"):
        graph._load_company_profile("尽调", state)


def test_static_profile_path_remains_available(monkeypatch: pytest.MonkeyPatch):
    static = _profile(name="静态档案有限公司")
    state = create_initial_state("请对静态档案有限公司尽调", "legacy-run", due_diligence=True)
    graph = _graph_without_adapters(monkeypatch)
    monkeypatch.setattr(profile_module, "find_company", lambda _query: static)

    company = graph._load_company_profile(state["query"], state)
    assert company is static
    assert state["company_name"] == "静态档案有限公司"
    assert state["admin_profile_ref"] == {}
    assert not any(check["scope"].startswith("scenario:") for check in state["field_checks"])


def test_v2_service_forwards_snapshot_parameters_and_snapshot_path_never_touches_personal_kb(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    class StubGraph:
        max_iterations = 3

        async def run(self, _query, _session_id, **kwargs):
            captured.update(kwargs)
            yield {"type": "done"}

    service = object.__new__(DeepResearchV2Service)
    service.graph = StubGraph()
    snapshot = _snapshot(_profile())

    async def collect():
        return [item async for item in service.research(
            "尽调", session_id="forward-run", search_web=False, search_local=False,
            provided_company_profile=snapshot["profile"],
            admin_profile_ref=snapshot["ref"], admin_profile_scenario="factoring",
        )]

    output = asyncio.run(collect())
    assert captured["provided_company_profile"] == snapshot["profile"]
    assert captured["admin_profile_ref"] == snapshot["ref"]
    assert captured["admin_profile_scenario"] == "factoring"
    assert output[-1] == "data: [DONE]\n\n"

    # The explicit profile helper has no route through the user-scoped KB or
    # Milvus.  Make either accidental touch a hard test failure.
    monkeypatch.setattr(research_router, "resolve_kb_scope", lambda *_args: pytest.fail("不得解析个人 KB"))
    _install_profile_service(monkeypatch, lambda _db, _profile_id: snapshot)
    assert research_router._load_admin_company_profile_snapshot(object(), snapshot["ref"]["id"])["ref"] == snapshot["ref"]
