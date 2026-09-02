"""Public managed-profile references must stay safe and consistent in SSE."""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from typing import Any

import pytest


BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(BACKEND, "app")
for path in (BACKEND, APP):
    if path not in sys.path:
        sys.path.insert(0, path)

from service.deep_research_v2 import graph as graph_module  # noqa: E402
from service.deep_research_v2.graph import (  # noqa: E402
    DeepResearchGraph,
    build_complete_event,
    public_profile_ref,
)


def _managed_ref() -> dict[str, Any]:
    return {
        "id": "profile-123",
        "revision": 7,
        "content_sha256": "a" * 64,
        "source": "admin_company_profile",
        # These mimic checkpoint-only details that must never cross SSE.
        "snapshot_binding": {"mac": "private-binding"},
        "internal_note": "private",
    }


def _public_ref() -> dict[str, Any]:
    return {
        "id": "profile-123",
        "revision": 7,
        "content_sha256": "a" * 64,
        "source": "admin_company_profile",
    }


async def _collect(events):
    return [event async for event in events]


def test_public_profile_ref_whitelists_and_detaches_values():
    ref = _managed_ref()

    projected = public_profile_ref(ref)

    assert projected == _public_ref()
    assert projected is not ref
    assert set(projected) == {"id", "revision", "content_sha256", "source"}
    assert public_profile_ref({}) is None
    assert public_profile_ref("not-a-ref") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ""),
        ("revision", True),
        ("revision", 0),
        ("revision", "7"),
        ("content_sha256", "not-a-digest"),
        ("content_sha256", "A" * 64),
        ("source", "internal_profile_store"),
    ],
)
def test_public_profile_ref_rejects_malformed_or_non_admin_identity(field, value):
    ref = _managed_ref()
    ref[field] = value

    assert public_profile_ref(ref) is None


def test_complete_event_has_public_ref_for_managed_and_none_for_static_profile():
    managed_state = {"admin_profile_ref": _managed_ref()}

    managed = build_complete_event(managed_state, [])
    static = build_complete_event({"admin_profile_ref": {}}, [])

    assert managed["profile_ref"] == _public_ref()
    assert set(managed["profile_ref"]) == set(_public_ref())
    assert static["profile_ref"] is None


def test_start_and_profile_loaded_have_the_same_public_ref(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "profile-ref-event-test-key")
    graph = object.__new__(DeepResearchGraph)
    graph.max_iterations = 3
    graph.checkpoint_service = None
    graph._checkpoint_integrity_mode = lambda *_args: "managed"
    graph._restore_managed_profile_snapshot = lambda restored, *_args, **_kwargs: restored
    graph._seal_graph_state = lambda restored, *_args: restored

    def _load_company_profile(_query, state):
        state["company_name"] = "受控企业"
        state["facts"] = [{"id": "profile-fact"}]
        return {"name": state["company_name"]}

    async def _run_with_langgraph(_state):
        yield {"type": "graph_driven"}

    graph._load_company_profile = _load_company_profile
    graph._run_with_langgraph = _run_with_langgraph

    events = asyncio.run(_collect(graph.run(
        "尽调", "start-session", due_diligence=True,
        provided_company_profile={"name": "受控企业"},
        admin_profile_ref=_managed_ref(),
    )))

    start, loaded = events[:2]
    assert start["type"] == "research_start"
    assert loaded["type"] == "company_profile_loaded"
    assert start["profile_ref"] == loaded["profile_ref"] == _public_ref()
    assert set(start["profile_ref"]) == set(_public_ref())


@pytest.mark.parametrize(
    ("state_ref", "expected"),
    [(_managed_ref(), _public_ref()), ({}, None)],
    ids=["managed", "static"],
)
def test_ordinary_resume_has_the_same_public_ref(
    state_ref: dict[str, Any], expected: dict[str, Any] | None,
):
    state = {"phase": "researching", "admin_profile_ref": copy.deepcopy(state_ref)}
    graph = object.__new__(DeepResearchGraph)
    graph.graph = None
    graph.checkpoint_service = None
    graph._load_checkpoint = lambda _session_id: copy.deepcopy(state)
    graph._checkpoint_integrity_mode = lambda *_args: "managed"
    graph._restore_managed_profile_snapshot = lambda restored, *_args, **_kwargs: restored
    graph._seal_graph_state = lambda restored, *_args: restored

    async def _run_with_langgraph(_state):
        yield {"type": "graph_driven"}

    graph._run_with_langgraph = _run_with_langgraph

    events = asyncio.run(_collect(graph.run("query", "resume-session", resume=True)))

    resumed = events[0]
    assert resumed["type"] == "research_resumed"
    assert resumed["profile_ref"] == expected
    if expected is not None:
        assert set(resumed["profile_ref"]) == set(expected)


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


def test_human_resume_has_the_same_public_ref(monkeypatch: pytest.MonkeyPatch):
    state = {"admin_profile_ref": _managed_ref(), "risk_assessment": {}}
    graph = object.__new__(DeepResearchGraph)
    graph.graph = _ReviewGraph(copy.deepcopy(state))
    graph.checkpoint_service = None
    graph._checkpoint_integrity_mode = lambda *_args: "managed"
    graph._restore_managed_profile_snapshot = lambda restored, *_args, **_kwargs: restored
    graph._seal_graph_state = lambda restored, *_args: restored
    monkeypatch.setattr(graph_module, "LANGGRAPH_AVAILABLE", True)
    monkeypatch.setattr(graph_module, "apply_human_review", lambda assessment, _decision: assessment)

    async def _drive(_payload, _session_id, user_id=None):
        yield {"type": "graph_driven"}

    graph._drive = _drive

    events = asyncio.run(_collect(graph.resume_review(
        "review-session", {"approved": True}, user_id="reviewer",
    )))

    resumed = events[0]
    assert resumed["type"] == "research_resumed"
    assert resumed["reason"] == "human_review"
    assert resumed["profile_ref"] == _public_ref()
    assert set(resumed["profile_ref"]) == set(_public_ref())


class _Interrupt:
    def __init__(self, value: dict[str, Any]):
        self.value = value


class _InterruptingGraph:
    def __init__(self, final_state: dict[str, Any]):
        self.final_state = final_state

    async def astream(self, _payload, _config, stream_mode):
        yield "values", self.final_state
        yield "values", {
            "__interrupt__": [_Interrupt({
                # No interrupt payload field is authoritative at the SSE
                # boundary, including its event type and provenance.
                "type": "research_complete",
                "profile_ref": {"id": "forged", "internal_note": "leak"},
                "private_checkpoint_field": "must-not-leak",
            })]
        }


class _Checkpoint:
    def __init__(self):
        self.statuses: list[str] = []

    def update_status(self, _session_id, status, error_message=None):
        self.statuses.append(status)
        return True


def test_human_review_required_uses_current_state_not_interrupt_payload():
    state = {"admin_profile_ref": _managed_ref(), "session_id": "review-session"}
    graph = object.__new__(DeepResearchGraph)
    graph.graph = _InterruptingGraph(state)
    graph.checkpoint_service = _Checkpoint()

    request = DeepResearchGraph._review_request(state, {})
    events = asyncio.run(_collect(graph._drive(state, "review-session")))

    assert request["profile_ref"] == _public_ref()
    required = events[-1]
    assert required["type"] == "human_review_required"
    assert required["profile_ref"] == _public_ref()
    assert required["profile_ref"]["id"] != "forged"
    assert set(required["profile_ref"]) == set(_public_ref())
    assert "private_checkpoint_field" not in required
