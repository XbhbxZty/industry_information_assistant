"""D4b2b real PostgreSQL preparation and actual HTTP/graph review integration.

Only model/search agents and authentication-token decoding are replaced. Claim
authorization reads real database users; checkpoints/claims use a disposable PG
database and the workflow uses real LangGraph with a test MemorySaver.
"""
import asyncio
import copy
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from test_review_claim_service_postgres import (
    pending, context_db, keys, disposable_postgres_database,  # noqa: F401
    SESSION, APPROVE, _basis, _claim_state, _expire,
)
from test_graph_equivalence import _build_graph
from models.user import User
from service.review_claim_service import (
    ReviewClaimService, ReviewClaimConflict, ReviewClaimForbidden,
    ReviewClaimInvalidDecision,
)
from service.checkpoint_integrity import GRAPH_SEAL_FIELD, MODE_STANDARD, issue_graph_state_seal
from service.deep_research_v2.service import DeepResearchV2Service
from router.auth_router import get_current_user_required


router_module = importlib.import_module("router.research_router")
auth_module = importlib.import_module("router.auth_router")
pytestmark = pytest.mark.postgres_integration


async def _collect(stream):
    return [item async for item in stream]


def _flow(pending, mode="standard"):
    assert pending.checkpoints.delete_checkpoint(SESSION)  # Unclaimed fixture target only.
    graph = _build_graph(requires_review=True)
    graph.checkpoint_service = pending.checkpoints
    kwargs = {}
    query = "普通行业研究"
    if mode == "managed":
        from test_admin_profile_resume_integrity import _snapshot, QUERY
        profile, ref, scenario = _snapshot()
        kwargs = dict(due_diligence=True, provided_company_profile=profile,
                      admin_profile_ref=ref, admin_profile_scenario=scenario)
        query = QUERY
    events = asyncio.run(_collect(graph.run(query, SESSION, user_id=str(pending.users["owner"]), **kwargs)))
    assert events[-1]["type"] == "human_review_required"
    return graph


def _client(pending, graph, monkeypatch, *, actor="one", claim_service=None, forge_role=False):
    with Session(pending.engine) as db:
        row = db.query(User).filter(User.id == pending.users[actor]).one()
        user = SimpleNamespace(id=row.id, username="stale token username", is_active=True,
                               is_superuser=True if forge_role else row.is_superuser)
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_current_user_required] = lambda: user

    def database():
        with Session(pending.engine) as db:
            yield db

    app.dependency_overrides[router_module.get_db] = database
    app.dependency_overrides[router_module.get_review_claim_service] = lambda: claim_service or pending.service
    monkeypatch.setattr(auth_module, "REVIEWER_POLICY", pending.policy)
    service = DeepResearchV2Service.__new__(DeepResearchV2Service)
    service.graph = graph
    monkeypatch.setattr(router_module, "get_research_service_v2", lambda: service)
    return TestClient(app)


def _events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]


def test_prepare_is_atomic_and_retries_keep_server_identity_and_original_decision(pending):
    before = _basis(pending)
    first = pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE)
    assert first.claim.state == "accepted" and first.claim.decision_json["reviewer"] == "one"
    saved = _claim_state(pending)
    assert pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE) == first
    assert _claim_state(pending) == saved and _basis(pending) == before
    assert pending.engine.pool.checkedout() == 0
    for actor, decision in (("two", APPROVE), ("one", {"approved": False, "comment": "changed"})):
        with pytest.raises(ReviewClaimConflict):
            pending.service.prepare_submission(SESSION, pending.users[actor], decision)
    assert _claim_state(pending) == saved


@pytest.mark.parametrize("decision", [
    {"approved": False}, {"approved": True, "override_level": "高风险"},
    {"approved": False, "comment": "no", "override_level": "高风险"},
    {"approved": True, "override_level": "invented"},
])
def test_invalid_decision_does_not_leave_an_automatic_claim(pending, decision):
    before = _basis(pending)
    with pytest.raises(ReviewClaimInvalidDecision):
        pending.service.prepare_submission(SESSION, pending.users["one"], decision)
    assert _claim_state(pending) is None and _basis(pending) == before


def test_read_submission_rechecks_authority_and_can_renew_only_same_accepted_claim(pending):
    first = pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE)
    _expire(pending)
    renewed = pending.service.read_submission(SESSION, pending.users["one"], first.claim.token, renew=True)
    assert renewed.claim.lease_expires_at > first.claim.lease_expires_at
    assert renewed.claim.accepted_at == first.claim.accepted_at
    assert renewed.claim.decision_json == first.claim.decision_json
    with pytest.raises(ReviewClaimConflict):
        pending.service.read_submission(SESSION, pending.users["two"], first.claim.token, renew=True)
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE users SET is_active=false WHERE id=:id"), {"id": pending.users["one"]})
    with pytest.raises(ReviewClaimForbidden):
        pending.service.read_submission(SESSION, pending.users["one"], first.claim.token, renew=True)


def test_two_reviewers_preparing_have_exactly_one_accepted_decision(pending):
    barrier = Barrier(2)
    def prepare(actor):
        barrier.wait(timeout=10)
        try:
            return pending.service.prepare_submission(SESSION, pending.users[actor], APPROVE)
        except ReviewClaimConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=15) for f in (pool.submit(prepare, "one"), pool.submit(prepare, "two"))]
    assert sum(result is not None for result in results) == 1
    assert _claim_state(pending)["state"] == "accepted"


@pytest.mark.parametrize("mode", ["standard", "managed"])
@pytest.mark.parametrize("decision", [APPROVE, {"approved": False, "comment": "待补确权"},
                                      {"approved": True, "comment": "改判理由", "override_level": "高风险"}])
def test_existing_http_review_button_uses_atomic_graph_path_and_replays_completion(pending, monkeypatch, mode, decision):
    graph = _flow(pending, mode)
    client = _client(pending, graph, monkeypatch)
    before = _basis(pending)
    response = client.post(f"/research/review/{SESSION}", json=decision)
    assert response.status_code == 200
    events = _events(response)
    assert not any(e["type"] == "error" for e in events), events
    assert [e["type"] for e in events][-2:] == ["human_review_completed", "research_complete"]
    saved, claim = _basis(pending), _claim_state(pending)
    assert saved[2] == "completed" and claim["state"] == "finalized"
    assert saved[4] == before[4] + 1
    completed = events[-1]
    assert completed["final_report"] == saved[3]["final_report"]
    assert completed["risk_assessment"] == saved[3]["risk_assessment"]
    assert completed["risk_assessment"]["human_review"]["reviewer"] == "one"  # Actual DB identity.
    assert completed["risk_assessment"]["human_review"]["reviewed_at"] == claim["accepted_at"].isoformat()
    assert claim["token"] not in response.text and claim["basis_seal"] not in response.text
    assert "state_json" not in response.text and "ui_state_json" not in response.text
    assert pending.engine.pool.checkedout() == 0
    retry = client.post(f"/research/review/{SESSION}", json=decision)
    assert retry.status_code == 200 and _events(retry)[-1] == completed
    assert _basis(pending) == saved and _claim_state(pending) == claim
    assert client.post(f"/research/review/{SESSION}", json={"approved": False, "comment": "different"}).status_code == 409


@pytest.mark.parametrize("actor", ["owner", "outsider", "disabled"])
def test_http_cannot_spoof_database_reviewer_authority(pending, monkeypatch, actor):
    client = _client(pending, None, monkeypatch, actor=actor, forge_role=True)
    response = client.post(f"/research/review/{SESSION}", json=APPROVE)
    assert response.status_code == 403
    assert _claim_state(pending) is None


@pytest.mark.parametrize("decision", [
    {"approved": "true"}, {"approved": 1}, {"approved": True, "reviewer": "forged"},
    {"approved": True, "reviewed_at": "2020-01-01"}, {"approved": True, "token": "forged"},
    {"approved": False},
])
def test_invalid_http_input_fails_before_sse_and_does_not_claim(pending, monkeypatch, decision):
    client = _client(pending, None, monkeypatch)
    response = client.post(f"/research/review/{SESSION}", json=decision)
    assert response.status_code == 422
    assert "text/event-stream" not in response.headers.get("content-type", "")
    assert _claim_state(pending) is None


def test_http_database_error_is_safe_503_before_opening_stream(pending, monkeypatch):
    def factory():
        raise RuntimeError("private-secret-database-connection")
    broken = ReviewClaimService(session_factory=factory, reviewer_policy=pending.policy)
    client = _client(pending, None, monkeypatch, claim_service=broken)
    response = client.post(f"/research/review/{SESSION}", json=APPROVE)
    assert response.status_code == 503 and "private-secret" not in response.text


@pytest.mark.parametrize("hook,committed", [("before_commit", False), ("after_commit", True)])
def test_http_failed_final_commit_never_emits_completion_and_retry_recovers(pending, monkeypatch, hook, committed):
    graph = _flow(pending)
    client = _client(pending, graph, monkeypatch)
    original = pending.service.finalize
    first = True

    def flaky(*args, **kwargs):
        nonlocal first
        if not first:
            return original(*args, **kwargs)
        first = False
        def factory():
            db = Session(pending.engine)
            def fail(*_args):
                raise OperationalError("commit", {}, RuntimeError("private-commit-detail"))
            event.listen(db, hook, fail, once=True)
            return db
        failing = ReviewClaimService(session_factory=factory, reviewer_policy=pending.policy)
        return failing.finalize(*args, **kwargs)

    monkeypatch.setattr(pending.service, "finalize", flaky)
    failed = client.post(f"/research/review/{SESSION}", json=APPROVE)
    events = _events(failed)
    assert failed.status_code == 200 and any(e["type"] == "error" for e in events)
    assert not any(e["type"] in {"human_review_completed", "research_complete"} for e in events)
    assert "private-commit-detail" not in failed.text
    assert _claim_state(pending)["state"] == ("finalized" if committed else "accepted")
    assert _basis(pending)[2] == ("completed" if committed else "paused")
    retry = client.post(f"/research/review/{SESSION}", json=APPROVE)
    assert _events(retry)[-1]["type"] == "research_complete", _events(retry)
    assert _claim_state(pending)["state"] == "finalized"


def test_missing_graph_checkpoint_recovers_only_deterministic_last_step(pending, monkeypatch):
    _flow(pending)
    fresh_graph = _build_graph(requires_review=True)  # Empty MemorySaver, business DB remains.
    fresh_graph.checkpoint_service = pending.checkpoints
    client = _client(pending, fresh_graph, monkeypatch)
    response = client.post(f"/research/review/{SESSION}", json=APPROVE)
    events = _events(response)
    assert events[-1]["type"] == "research_complete", events
    assert not any(e["type"] in {"report_chunk", "search_result_item", "outline_updated"} for e in events)


@pytest.mark.parametrize("stop_after", ["research_resumed", "human_review_completed"])
def test_closing_review_stream_does_not_release_or_fail_accepted_work(pending, stop_after):
    graph = _flow(pending)
    prepared = pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE)
    async def interrupt():
        stream = graph.resume_claimed_review(SESSION, str(pending.users["one"]), prepared.claim.token, pending.service)
        async for item in stream:
            if item["type"] == stop_after:
                await stream.aclose()
                return
        pytest.fail(f"expected event not emitted: {stop_after}")
    asyncio.run(interrupt())
    assert _claim_state(pending)["state"] == ("accepted" if stop_after == "research_resumed" else "finalized")
    assert _basis(pending)[2] != "failed"
    events = asyncio.run(_collect(graph.resume_claimed_review(
        SESSION, str(pending.users["one"]), prepared.claim.token, pending.service,
    )))
    assert events[-1]["type"] == "research_complete", events


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("reseal", [False, True])
def test_damaged_or_validly_drifted_graph_is_not_treated_as_missing(pending, monkeypatch, finished, reseal):
    graph = _flow(pending)
    client = _client(pending, graph, monkeypatch)
    if finished:
        original = pending.service.finalize
        def fail(*args, **kwargs):
            raise RuntimeError("test final transaction unavailable")
        monkeypatch.setattr(pending.service, "finalize", fail)
        response = client.post(f"/research/review/{SESSION}", json=APPROVE)
        assert _events(response)[-1]["type"] == "error"
        monkeypatch.setattr(pending.service, "finalize", original)
    before = _basis(pending)

    async def drift():
        config = {"configurable": {"thread_id": SESSION}}
        snapshot = await graph.graph.aget_state(config)
        assert snapshot.next == (() if finished else ("human_review",))
        values = dict(snapshot.values)
        values["final_report"] += "\nunauthorized report change"
        change = {"final_report": values["final_report"]}
        if reseal:
            change[GRAPH_SEAL_FIELD] = issue_graph_state_seal(values, MODE_STANDARD)
        await graph.graph.aupdate_state(config, change)

    asyncio.run(drift())
    response = client.post(f"/research/review/{SESSION}", json=APPROVE)
    events = _events(response)
    assert events[-1]["type"] == "error", events
    assert not any(item["type"] in {"human_review_completed", "research_complete"} for item in events)
    assert _claim_state(pending)["state"] == "accepted" and _basis(pending) == before


def test_two_graph_instances_resuming_same_acceptance_commit_one_result(pending):
    graph = _flow(pending)
    other = copy.copy(graph)
    other.graph = other._build_langgraph()  # Separate compiled graph, same MemorySaver.
    assert other.graph is not graph.graph
    submission = pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE)
    before = _basis(pending)

    async def compete():
        streams = [instance.resume_claimed_review(
            SESSION, str(pending.users["one"]), submission.claim.token, pending.service,
        ) for instance in (graph, other)]
        # Both instances revalidate the pending graph before either advances it.
        for stream in streams:
            assert (await anext(stream))["type"] == "research_resumed"
        return await asyncio.gather(*(_collect(stream) for stream in streams))

    results = asyncio.run(compete())
    assert all(events[-1]["type"] == "research_complete" for events in results), results
    assert results[0][-1] == results[1][-1]
    assert _basis(pending)[4] == before[4] + 1 and _claim_state(pending)["state"] == "finalized"


def test_two_http_reviewers_cannot_both_accept(pending, monkeypatch):
    graph = _flow(pending)
    clients = [_client(pending, graph, monkeypatch, actor=actor) for actor in ("one", "two")]
    barrier = Barrier(2)
    def submit(client):
        barrier.wait(timeout=10)
        return client.post(f"/research/review/{SESSION}", json=APPROVE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, clients))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response for response in results if response.status_code == 200)
    assert _events(winner)[-1]["type"] == "research_complete", _events(winner)
    assert _claim_state(pending)["state"] == "finalized"


def test_claimed_drive_filters_early_custom_completion_on_finalization_failure(pending, monkeypatch):
    graph = _flow(pending)
    submission = pending.service.prepare_submission(SESSION, pending.users["one"], APPROVE)
    candidate = graph._claimed_review_candidate(
        submission.state_json, submission.claim.decision_json,
        submission.claim.accepted_at.isoformat(), MODE_STANDARD,
    )
    async def forged_stream(*args, **kwargs):
        yield "custom", {"type": "human_review_completed", "content": "not committed"}
        yield "custom", {"type": "research_complete", "content": "not committed"}
        yield "values", candidate
    def fail(*args, **kwargs):
        raise RuntimeError("test final transaction unavailable")
    graph.graph = SimpleNamespace(astream=forged_stream)
    monkeypatch.setattr(pending.service, "finalize", fail)
    events = asyncio.run(_collect(graph._drive({}, SESSION, review_context={
        "claim_service": pending.service, "reviewer_id": str(pending.users["one"]),
        "token": submission.claim.token,
    })))
    assert [item["type"] for item in events] == ["error"]
    assert _claim_state(pending)["state"] == "accepted" and _basis(pending)[2] == "paused"
