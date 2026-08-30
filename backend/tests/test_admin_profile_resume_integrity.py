"""Stage 3.3A: managed company-profile checkpoint restore boundary tests.

These tests deliberately use a bare graph and in-memory fakes: the contract is
the restore boundary, not a database, an LLM, or a real LangGraph execution.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from pathlib import Path
from typing import Any

import pytest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

from service import company_profile as profile_module  # noqa: E402
from service import datasource  # noqa: E402
from service.deep_research_v2 import graph as graph_module  # noqa: E402
from service.deep_research_v2.graph import DeepResearchGraph  # noqa: E402
from service.deep_research_v2.state import create_initial_state  # noqa: E402
from config.dd_checklist import compute_completeness  # noqa: E402
from service.checkpoint_integrity import MODE_MANAGED, verify_graph_state_seal  # noqa: E402
from service.risk_scorecard import PROFILE_BACKED_FIELDS  # noqa: E402
from service.verification import build_scoring_view, record_structured_evidence  # noqa: E402


SESSION = "managed-resume-session"
QUERY = "请对恢复受控企业有限公司开展尽调"


@pytest.fixture(autouse=True)
def _managed_snapshot_hmac_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "stage-33-test-server-key")


def _snapshot() -> tuple[dict[str, Any], dict[str, Any], str]:
    ref = {
        "id": "0dbd5029-443f-4364-9049-103962492171",
        "revision": 7,
        "content_sha256": "a" * 64,
        "source": "admin_company_profile",
    }
    source = {
        "source_id": "managed-registration-source",
        "name": "工商登记档案",
        "issuer": "登记机关",
        "source_type": "official",
        "field_ids": ["registration"],
        "retrieved_at": "2026-08-20T00:00:00+00:00",
        "as_of_date": "2026-08-20",
        "reference": "managed://registration",
        "sha256": None,
    }
    profile = {
        "company_id": "managed-resume-co",
        "name": "恢复受控企业有限公司",
        "credit_code": "91000000TEST3301",
        "coverage": {"queried": ["registration"], "retrieved_at": source["retrieved_at"]},
        "registration": {
            "registered_capital": "100万元",
            "paid_in_capital": "100万元",
            "established_date": "2020-01-01",
            "legal_representative": "张三",
            "company_type": "有限责任公司",
            "operating_status": "存续",
            "business_scope": "技术服务",
        },
        "financials": [],
        "judicial_records": [],
        "_admin_profile_ref": copy.deepcopy(ref),
        "_scenario_data": {},
        "_admin_field_sources": [source],
    }
    return profile, ref, "factoring"


def _bare_graph(monkeypatch: pytest.MonkeyPatch) -> DeepResearchGraph:
    graph = object.__new__(DeepResearchGraph)
    graph.max_iterations = 3
    monkeypatch.setattr(datasource, "apply_all", lambda company, checks, store: {})
    return graph


def _managed_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scoring_view: bool = False,
    for_review: bool = False,
) -> dict[str, Any]:
    profile, ref, scenario = _snapshot()
    state = create_initial_state(
        QUERY,
        SESSION,
        due_diligence=True,
        provided_company_profile=profile,
        admin_profile_ref=ref,
        admin_profile_scenario=scenario,
    )
    graph = _bare_graph(monkeypatch)
    graph._load_company_profile(QUERY, state)
    if scoring_view:
        view, unmergeable = build_scoring_view(
            state["company_profile"],
            state["field_checks"],
            state["evidence_store"],
            profile_backed_fields=PROFILE_BACKED_FIELDS,
            profile_replay_fn=profile_module.replay_from_profile,
        )
        assert not unmergeable
        state["scoring_view"] = view
    if for_review:
        state["risk_assessment"] = {"level": "高风险"}
    graph._seal_graph_state(state, MODE_MANAGED)
    return state


async def _collect(generator):
    return [item async for item in generator]


def _normal_resume_graph(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any] | None,
    ran: list[dict[str, Any]],
    *,
    integrity_mode: str | None = MODE_MANAGED,
) -> DeepResearchGraph:
    graph = _bare_graph(monkeypatch)
    graph._load_checkpoint = lambda _session_id: copy.deepcopy(state) if state is not None else None

    class _IntegrityMetadata:
        def get_checkpoint_integrity_mode(self, _session_id):
            return integrity_mode

    graph.checkpoint_service = _IntegrityMetadata()

    async def _run_with_langgraph(restored: dict[str, Any]):
        ran.append(restored)
        yield {"type": "graph_driven"}

    graph._run_with_langgraph = _run_with_langgraph
    return graph


def _mutate(state: dict[str, Any], kind: str) -> None:
    if kind == "profile":
        state["provided_company_profile"]["name"] = "被篡改的主体"
    elif kind == "ref":
        state["admin_profile_ref"]["revision"] = 8
    elif kind == "source":
        state["provided_company_profile"]["_admin_field_sources"][0]["reference"] = "tampered://source"
    elif kind == "scenario":
        state["admin_profile_scenario"] = ""
    elif kind == "company_profile":
        state["company_profile"]["name"] = "被篡改的评分主体"
    elif kind == "binding":
        state["admin_profile_snapshot_binding"]["mac"] = "0" * 64
    elif kind == "missing_binding":
        state.pop("admin_profile_snapshot_binding")
    elif kind == "missing_payload_hash":
        state.pop("admin_profile_payload_sha256")
    elif kind == "payload_hash":
        state["admin_profile_payload_sha256"] = "0" * 64
    elif kind == "marker":
        state["company_profile"].pop("_admin_profile_snapshot_authorized")
    elif kind == "field_checks":
        state["field_checks"].pop(0)
    elif kind == "required_flag":
        state["field_checks"][0]["required"] = False
    elif kind == "not_applicable":
        state["field_checks"][0]["status"] = "not_applicable"
        state["completeness"] = compute_completeness(state["field_checks"])
    elif kind == "invalid_status":
        state["field_checks"][0]["status"] = "accepted"
        state["completeness"] = compute_completeness(state["field_checks"])
    elif kind == "profile_assertion":
        check = next(row for row in state["field_checks"] if row["field_id"] == "registration")
        check.update({
            "status": "unverified", "value": None,
            "verification_origin": "", "source_adapter": "",
        })
    elif kind == "structured_assertion":
        # Create a valid, trusted adapter assertion first, then simulate a
        # checkpoint that downgraded only the check while retaining evidence.
        datasource.register_all()
        check = next(row for row in state["field_checks"] if row["field_id"] == "guarantee")
        record_structured_evidence(
            state["evidence_store"], check,
            source_adapter="relation_registry",
            status="verified",
            value="经查询，无相关记录",
            raw={"guarantee": []},
            retrieved_at="2026-08-20T00:00:00+00:00",
            as_of_date="2026-08-20",
        )
        check.update({
            "status": "unverified", "value": None,
            "verification_origin": "", "source_adapter": "",
            "evidence_ids": [],
        })
        state["completeness"] = compute_completeness(state["field_checks"])
    elif kind == "structured_erasure":
        datasource.register_all()
        check = next(row for row in state["field_checks"] if row["field_id"] == "guarantee")
        evidence_id = record_structured_evidence(
            state["evidence_store"], check,
            source_adapter="relation_registry",
            status="verified",
            value="经查询，无相关记录",
            raw={"guarantee": []},
            retrieved_at="2026-08-20T00:00:00+00:00",
            as_of_date="2026-08-20",
        )
        state["evidence_store"].pop(evidence_id)
        check.update({
            "status": "unverified", "value": None,
            "verification_origin": "", "source_adapter": "",
            "evidence_ids": [],
        })
        state["completeness"] = compute_completeness(state["field_checks"])
        state["scoring_view"] = {}
    elif kind == "all_managed_traces":
        for key in (
            "provided_company_profile", "admin_profile_ref",
            "admin_profile_payload_sha256", "admin_profile_snapshot_binding",
            "admin_profile_scenario", "checkpoint_graph_seal",
            "_checkpoint_integrity_mode",
        ):
            state.pop(key, None)
        for key in (
            "_admin_profile_ref", "_scenario_data", "_admin_field_sources",
            "_admin_profile_snapshot_authorized",
        ):
            state["company_profile"].pop(key, None)
        for check in state["field_checks"]:
            check.pop("profile_sources", None)
    elif kind == "empty_scoring_view":
        state["scoring_view"] = {}
    elif kind == "risk_assessment":
        state["risk_assessment"] = {"level": "低风险", "composite_score": 0}
    elif kind == "completeness":
        state["completeness"]["required_total"] += 1
    elif kind == "scoring_view":
        state["scoring_view"]["name"] = "被篡改的评分视图"
    else:  # pragma: no cover - keeps parametrization errors explicit
        raise AssertionError(f"unknown mutation: {kind}")


def test_new_managed_state_writes_versioned_hmac_binding(monkeypatch: pytest.MonkeyPatch):
    state = _managed_state(monkeypatch)
    binding = state["admin_profile_snapshot_binding"]
    assert binding["version"] == 2
    assert binding["algorithm"] == "hmac-sha256"
    assert len(binding["mac"]) == 64


def test_binding_can_derive_a_separate_key_from_existing_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-server-secret")

    state = _managed_state(monkeypatch)

    assert state["admin_profile_snapshot_binding"]["version"] == 2


def test_normal_resume_revalidates_and_reissues_managed_capability(monkeypatch: pytest.MonkeyPatch):
    state = _managed_state(monkeypatch, scoring_view=True)
    ran: list[dict[str, Any]] = []
    graph = _normal_resume_graph(monkeypatch, state, ran)

    events = asyncio.run(_collect(graph.run(QUERY, SESSION, resume=True)))

    assert [event["type"] for event in events] == ["research_resumed", "graph_driven"]
    assert len(ran) == 1
    assert ran[0]["company_profile"]["_admin_profile_snapshot_authorized"] is True
    assert ran[0]["company_profile"] == profile_module.validate_admin_company_profile_snapshot(
        state["provided_company_profile"], state["admin_profile_ref"],
        state["admin_profile_scenario"], state["admin_profile_payload_sha256"],
    )


@pytest.mark.parametrize(
    "kind",
    ["profile", "ref", "source", "scenario", "company_profile", "marker", "binding",
     "missing_binding", "missing_payload_hash", "payload_hash", "field_checks",
     "required_flag", "not_applicable", "invalid_status", "profile_assertion",
     "structured_assertion", "structured_erasure", "all_managed_traces",
     "completeness", "scoring_view", "empty_scoring_view", "risk_assessment"],
)
def test_normal_resume_rejects_every_managed_snapshot_mutation(
    monkeypatch: pytest.MonkeyPatch, kind: str,
):
    state = _managed_state(monkeypatch, scoring_view=True)
    _mutate(state, kind)
    ran: list[dict[str, Any]] = []
    graph = _normal_resume_graph(monkeypatch, state, ran)

    with pytest.raises(
        ValueError,
        match="管理端企业档案|company_profile|completeness|scoring_view|完整性",
    ):
        asyncio.run(_collect(graph.run(QUERY, SESSION, resume=True)))
    assert not ran


def test_normal_resume_missing_checkpoint_never_starts_new_name_matched_run(monkeypatch: pytest.MonkeyPatch):
    ran: list[dict[str, Any]] = []
    graph = _normal_resume_graph(monkeypatch, None, ran)
    graph._load_company_profile = lambda *_args: pytest.fail("resume 不得创建新档案或名称匹配")

    with pytest.raises(ValueError, match="未能加载"):
        asyncio.run(_collect(graph.run(QUERY, SESSION, resume=True)))
    assert not ran


def test_normal_resume_rejects_cross_session_replay(monkeypatch: pytest.MonkeyPatch):
    state = _managed_state(monkeypatch, scoring_view=True)
    ran: list[dict[str, Any]] = []
    graph = _normal_resume_graph(monkeypatch, state, ran)

    with pytest.raises(ValueError, match="session_id|完整性"):
        asyncio.run(_collect(graph.run(QUERY, "different-session", resume=True)))
    assert not ran


def test_static_legacy_checkpoint_remains_resumable(monkeypatch: pytest.MonkeyPatch):
    state = create_initial_state("静态历史档案", "static-resume", due_diligence=True)
    state.pop("checkpoint_graph_seal", None)
    state.pop("_checkpoint_integrity_mode", None)
    ran: list[dict[str, Any]] = []
    graph = _normal_resume_graph(monkeypatch, state, ran, integrity_mode=None)

    events = asyncio.run(_collect(graph.run("静态历史档案", "static-resume", resume=True)))

    assert [event["type"] for event in events] == ["research_resumed", "graph_driven"]
    assert len(ran) == 1


class _Snapshot:
    def __init__(self, values: dict[str, Any]):
        self.values = values
        self.next = ("human_review",)


class _ReviewGraph:
    def __init__(self, values: dict[str, Any]):
        self.snapshot = _Snapshot(values)
        self.updated: list[dict[str, Any]] = []

    async def aget_state(self, _config):
        return self.snapshot

    async def aupdate_state(self, _config, values):
        self.updated.append(values)


def _human_resume_graph(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> tuple[DeepResearchGraph, _ReviewGraph, list[Any]]:
    graph = _bare_graph(monkeypatch)
    review_graph = _ReviewGraph(copy.deepcopy(state))
    graph.graph = review_graph
    graph.checkpoint_service = type(
        "_IntegrityMetadata", (),
        {"get_checkpoint_integrity_mode": lambda self, _session_id: MODE_MANAGED},
    )()
    driven: list[Any] = []

    async def _drive(payload, _session_id, user_id=None):
        driven.append((payload, user_id))
        yield {"type": "graph_driven"}

    graph._drive = _drive
    return graph, review_graph, driven


def test_human_resume_revalidates_before_accepting_decision(monkeypatch: pytest.MonkeyPatch):
    state = _managed_state(monkeypatch, scoring_view=True, for_review=True)
    graph, review_graph, driven = _human_resume_graph(monkeypatch, state)
    monkeypatch.setattr(graph_module, "apply_human_review", lambda assessment, decision: assessment)

    events = asyncio.run(_collect(graph.resume_review(
        SESSION, {"approved": True, "reviewer": "复核人", "comment": "同意"}, user_id="u1",
    )))

    assert [event["type"] for event in events] == ["research_resumed", "graph_driven"]
    assert review_graph.updated and review_graph.updated[0]["company_profile"]["_admin_profile_snapshot_authorized"]
    assert driven


@pytest.mark.parametrize(
    "kind",
    ["profile", "ref", "source", "scenario", "company_profile", "marker", "binding",
     "missing_binding", "missing_payload_hash", "payload_hash", "field_checks",
     "required_flag", "not_applicable", "invalid_status", "profile_assertion",
     "structured_assertion", "structured_erasure", "all_managed_traces",
     "completeness", "scoring_view", "empty_scoring_view", "risk_assessment"],
)
def test_human_resume_rejects_tampered_snapshot_before_decision(
    monkeypatch: pytest.MonkeyPatch, kind: str,
):
    state = _managed_state(monkeypatch, scoring_view=True, for_review=True)
    _mutate(state, kind)
    graph, review_graph, driven = _human_resume_graph(monkeypatch, state)
    monkeypatch.setattr(
        graph_module, "apply_human_review",
        lambda *_args: pytest.fail("受损快照不得进入 decision 校验"),
    )

    events = asyncio.run(_collect(graph.resume_review(
        SESSION, {"approved": True, "reviewer": "复核人", "comment": "同意"}, user_id="u1",
    )))

    assert events and events[0]["type"] == "error"
    assert not review_graph.updated
    assert not driven


def test_real_langgraph_managed_interrupt_update_and_resume(monkeypatch: pytest.MonkeyPatch):
    """MemorySaver must preserve the interrupt after capability re-issuance.

    The lightweight fake tests the validation order; this one exercises the
    real ``aget_state -> aupdate_state -> Command(resume)`` scheduling path.
    """
    from test_graph_equivalence import _build_graph

    profile, ref, scenario = _snapshot()
    graph = _build_graph(requires_review=True)
    session_id = "managed-real-langgraph-review"

    first = asyncio.run(_collect(graph.run(
        QUERY,
        session_id,
        user_id="u1",
        due_diligence=True,
        provided_company_profile=profile,
        admin_profile_ref=ref,
        admin_profile_scenario=scenario,
    )))
    assert first[-1]["type"] == "human_review_required"
    paused = asyncio.run(graph.graph.aget_state({
        "configurable": {"thread_id": session_id},
    }))
    assert paused.next
    verify_graph_state_seal(dict(paused.values), MODE_MANAGED)

    second = asyncio.run(_collect(graph.resume_review(
        session_id,
        {"approved": True, "reviewer": "复核人", "comment": "同意"},
        user_id="u1",
    )))
    assert second[-1]["type"] == "research_complete"
    assert second[-1]["risk_assessment"]["human_review"]["reviewer"] == "复核人"
