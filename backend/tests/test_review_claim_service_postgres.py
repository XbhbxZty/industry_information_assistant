"""D4b1 short-transaction claims on identity-checked disposable PostgreSQL DBs.

These exercise the service directly. HTTP/SSE finalization is deliberately not
switched over until the next checkpoint; no test here claims that integration.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from test_checkpoint_context_postgres import context_db, keys, _service  # noqa: F401
from test_legacy_schema_preflight_postgres import disposable_postgres_database  # noqa: F401
from config.reviewer_policy import ReviewerPolicy
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity, ResearchReviewClaim
from models.user import User
from service.checkpoint_integrity import GRAPH_SEAL_FIELD, MODE_STANDARD, issue_graph_state_seal
from service.review_claim_service import (
    ReviewClaimService, ReviewClaimConflict, ReviewClaimForbidden,
    ReviewClaimIntegrityError, ReviewClaimInvalidDecision, ReviewClaimNotFound, ReviewClaimUnavailable,
)


pytestmark = pytest.mark.postgres_integration
SESSION = "short-claim-review"
APPROVE = {"approved": True, "comment": "已复核原文", "override_level": None}


def _pending_state():
    state = {
        "session_id": SESSION, "query": "claim test", "phase": "reviewing", "iteration": 1,
        "risk_assessment": {"requires_human_review": True, "level": "中风险", "composite_score": 60,
                            "gates_applied": [], "gate_kinds": []},
    }
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    return state


@pytest.fixture
def pending(context_db):
    identities = {name: uuid.uuid4() for name in ("owner", "one", "two", "outsider", "disabled", "admin")}
    with Session(context_db) as db:
        for name, user_id in identities.items():
            db.add(User(id=user_id, username=name, email=f"{name}@claims.test.invalid",
                        hashed_password="unused", is_active=name != "disabled",
                        is_superuser=name in {"disabled", "admin"}))
        db.commit()
    policy = ReviewerPolicy(frozenset(identities[name] for name in ("owner", "one", "two")))
    checkpoint_service = _service(context_db)
    state = _pending_state()
    assert checkpoint_service.save_checkpoint(SESSION, state, user_id=str(identities["owner"]))
    assert checkpoint_service.update_status(SESSION, "paused")
    return SimpleNamespace(
        engine=context_db, users=identities, policy=policy, checkpoints=checkpoint_service, state=state,
        service=ReviewClaimService(session_factory=lambda: Session(context_db), reviewer_policy=policy),
    )


def _basis(pending):
    with Session(pending.engine) as db:
        cp, integrity = db.query(ResearchCheckpoint).one(), db.query(ResearchCheckpointIntegrity).one()
        return (str(cp.id), str(cp.user_id), cp.status, copy.deepcopy(cp.state_json),
                integrity.business_revision, integrity.business_seal, copy.deepcopy(integrity.context_seal))


def _claim_state(pending):
    with Session(pending.engine) as db:
        claim = db.query(ResearchReviewClaim).one_or_none()
        return None if claim is None else {
            column.name: copy.deepcopy(getattr(claim, column.name)) for column in ResearchReviewClaim.__table__.columns
        }


def _expire(pending):
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE research_review_claims SET lease_expires_at=clock_timestamp()-interval '1 second'"))


def test_claim_commits_basis_before_return_and_holds_no_connection(pending):
    before = _basis(pending)
    receipt = pending.service.claim(SESSION, pending.users["one"])
    assert receipt.state == "claimed" and receipt.owner_id == str(pending.users["owner"])
    assert receipt.reviewer_id == str(pending.users["one"])
    assert receipt.basis_version == before[4] and receipt.basis_seal == before[5]
    assert len(receipt.token) == 43 and receipt.token not in repr(receipt)
    row = _claim_state(pending)
    assert row["token"] == receipt.token and row["decision_json"] is None
    assert receipt.lease_expires_at.tzinfo is not None
    assert _basis(pending) == before
    assert pending.engine.pool.checkedout() == 0
    with pending.engine.begin() as connection:
        # Completion of the method already released all its locks.
        for table in ("research_checkpoints", "research_checkpoint_integrities", "research_review_claims"):
            connection.execute(text(f"SELECT 1 FROM {table} FOR UPDATE NOWAIT"))


def test_two_independent_reviewers_have_exactly_one_winner(pending):
    gate = Barrier(2)

    def claim(name):
        service = ReviewClaimService(session_factory=lambda: Session(pending.engine), reviewer_policy=pending.policy)
        gate.wait(timeout=10)
        try:
            return service.claim(SESSION, pending.users[name])
        except ReviewClaimConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, name) for name in ("one", "two")]
        results = [future.result(timeout=15) for future in futures]
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert str(_claim_state(pending)["reviewer_id"]) == winners[0].reviewer_id


@pytest.mark.parametrize("table", ["research_checkpoints", "research_checkpoint_integrities", "research_review_claims"])
def test_busy_row_is_conflict_without_waiting_for_holder_to_release(pending, table):
    first = pending.service.claim(SESSION, pending.users["one"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Release the holder before joining workers even if the timeout fails.
        with pending.engine.begin() as holder:
            holder.execute(text(f"SELECT 1 FROM {table} FOR UPDATE"))
            call = pool.submit(pending.service.claim, SESSION, pending.users["one"], token=first.token)
            with pytest.raises(ReviewClaimConflict):
                call.result(timeout=5)  # Holder stays locked until the service rejects.
    assert _claim_state(pending)["token"] == first.token


def test_claim_retry_is_stable_and_release_is_idempotent(pending):
    one = pending.users["one"]
    receipt = pending.service.claim(SESSION, one)
    row = _claim_state(pending)
    assert pending.service.claim(SESSION, one) == receipt  # Lost response recovery.
    assert pending.service.claim(SESSION, one, token=receipt.token) == receipt
    assert _claim_state(pending) == row  # Retry does not silently renew.
    released = pending.service.release(SESSION, one, receipt.token)
    assert released.state == "released"
    assert pending.service.release(SESSION, one, receipt.token) == released
    replacement = pending.service.claim(SESSION, pending.users["two"])
    assert replacement.token != receipt.token
    with pytest.raises(ReviewClaimConflict):
        pending.service.accept(SESSION, one, receipt.token, APPROVE)


def test_expired_claim_rotates_token_and_never_revives_old_work(pending):
    one = pending.users["one"]
    old = pending.service.claim(SESSION, one)
    _expire(pending)
    for operation in (
        lambda: pending.service.claim(SESSION, one, token=old.token),
        lambda: pending.service.renew(SESSION, one, old.token),
        lambda: pending.service.accept(SESSION, one, old.token, APPROVE),
    ):
        with pytest.raises(ReviewClaimConflict):
            operation()
    new = pending.service.claim(SESSION, pending.users["two"])
    assert new.token != old.token and new.reviewer_id == str(pending.users["two"])
    for operation in (
        lambda: pending.service.release(SESSION, one, old.token),
        lambda: pending.service.renew(SESSION, one, old.token),
        lambda: pending.service.accept(SESSION, one, old.token, APPROVE),
    ):
        with pytest.raises(ReviewClaimConflict):
            operation()


def test_accept_freezes_server_identity_decision_and_time(pending):
    one = pending.users["one"]
    before = _basis(pending)
    claimed = pending.service.claim(SESSION, one)
    accepted = pending.service.accept(SESSION, one, claimed.token, APPROVE)
    assert accepted.state == "accepted" and accepted.accepted_at.tzinfo is not None
    assert accepted.finalized_at is None
    assert accepted.decision_json == {**APPROVE, "reviewer": "one", "reviewer_id": str(one)}
    assert accepted.decision_json["comment"] not in repr(accepted)
    assert len(accepted.decision_digest) == 64
    row = _claim_state(pending)
    assert pending.service.accept(SESSION, one, claimed.token, {**APPROVE, "comment": "  已复核原文  "}) == accepted
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE users SET username='renamed-one' WHERE id=:id"), {"id": one})
    assert pending.service.accept(SESSION, one, claimed.token, APPROVE) == accepted
    assert _claim_state(pending) == row
    assert _basis(pending) == before  # Accepted is not completed; no workflow write yet.
    accepted.decision_json["comment"] = "local mutation"
    assert _claim_state(pending)["decision_json"]["comment"] == APPROVE["comment"]
    with pytest.raises(ReviewClaimConflict):
        pending.service.accept(SESSION, one, claimed.token, {"approved": False, "comment": "different"})


def test_accepted_decision_never_changes_reviewer_after_lease_expiry(pending):
    one, two = pending.users["one"], pending.users["two"]
    claim = pending.service.claim(SESSION, one)
    accepted = pending.service.accept(SESSION, one, claim.token, APPROVE)
    _expire(pending)
    for operation in (
        lambda: pending.service.claim(SESSION, two),
        lambda: pending.service.release(SESSION, one, claim.token),
        lambda: pending.service.renew(SESSION, two, claim.token),
    ):
        with pytest.raises(ReviewClaimConflict):
            operation()
    retry = pending.service.accept(SESSION, one, claim.token, APPROVE)
    assert retry.accepted_at == accepted.accepted_at and retry.decision_digest == accepted.decision_digest
    renewed = pending.service.renew(SESSION, one, claim.token)
    assert renewed.lease_expires_at > datetime.now(timezone.utc)
    assert renewed.token == claim.token and renewed.accepted_at == accepted.accepted_at
    assert renewed.decision_json == accepted.decision_json


@pytest.mark.parametrize("decision", [
    {}, {"approved": "false"}, {"approved": 1}, {"approved": True, "comment": 123},
    {"approved": True, "override_level": ["低风险"]},
    {"approved": True, "reviewer": "forged"}, {"approved": True, "reviewer_id": "forged"},
    {"approved": True, "reviewed_at": "yesterday"},
    {"approved": True, "override_level": "fictional"},
    {"approved": True, "override_level": "低风险", "comment": "  "},
    {"approved": False}, {"approved": False, "comment": "拒绝", "override_level": "低风险"},
])
def test_invalid_decision_preserves_claim_and_workflow(pending, decision):
    claim = pending.service.claim(SESSION, pending.users["one"])
    before, row = _basis(pending), _claim_state(pending)
    with pytest.raises(ReviewClaimInvalidDecision):
        pending.service.accept(SESSION, pending.users["one"], claim.token, decision)
    assert _basis(pending) == before and _claim_state(pending) == row


@pytest.mark.parametrize("name", ["owner", "outsider", "disabled"])
def test_role_and_self_review_checked_from_database_on_every_call(pending, name):
    with pytest.raises(ReviewClaimForbidden):
        pending.service.claim(SESSION, pending.users[name])
    assert _claim_state(pending) is None
    receipt = pending.service.claim(SESSION, pending.users["one"])
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE users SET is_active=false WHERE id=:id"), {"id": pending.users["one"]})
    with pytest.raises(ReviewClaimForbidden):
        pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)
    assert _claim_state(pending)["state"] == "claimed"


def test_unauthorized_actor_cannot_probe_missing_or_busy_checkpoint(pending):
    outsider = pending.users["outsider"]
    with pytest.raises(ReviewClaimForbidden):
        pending.service.claim("missing", outsider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pending.engine.begin() as holder:
            holder.execute(text("SELECT 1 FROM research_checkpoints FOR UPDATE"))
            call = pool.submit(pending.service.claim, SESSION, outsider)
            with pytest.raises(ReviewClaimForbidden):
                call.result(timeout=5)


def test_superuser_uses_database_role_and_empty_comment_is_compatible(pending):
    receipt = pending.service.claim(SESSION, pending.users["admin"])
    accepted = pending.service.accept(SESSION, pending.users["admin"], receipt.token, {"approved": True, "comment": None})
    assert accepted.decision_json["comment"] == ""


@pytest.mark.parametrize("decision", [
    {"approved": False, "comment": "退回补充核实"},
    {"approved": True, "comment": "重新核对原文", "override_level": "低风险"},
])
def test_accept_means_received_not_approval_or_finalization(pending, decision):
    basis = _basis(pending)
    receipt = pending.service.claim(SESSION, pending.users["one"])
    accepted = pending.service.accept(SESSION, pending.users["one"], receipt.token, decision)
    assert accepted.state == "accepted" and accepted.finalized_at is None
    assert accepted.decision_json["approved"] is decision["approved"]
    assert _basis(pending) == basis


@pytest.mark.parametrize("operation", ["claim", "accept"])
def test_non_pending_but_validly_sealed_state_is_not_claimable(pending, operation):
    receipt = pending.service.claim(SESSION, pending.users["one"]) if operation == "accept" else None
    assert pending.checkpoints.update_status(SESSION, "completed")
    with pytest.raises(ReviewClaimConflict):
        if operation == "claim":
            pending.service.claim(SESSION, pending.users["one"])
        else:
            pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)


def test_missing_checkpoint_cannot_create_a_claim(pending):
    with pytest.raises(ReviewClaimNotFound):
        pending.service.claim("unknown", pending.users["one"])
    with pytest.raises(ReviewClaimConflict):
        pending.service.claim(SESSION, pending.users["one"], token="a" * 43)
    assert _claim_state(pending) is None


@pytest.mark.parametrize("change", ["status", "owner", "missing_integrity"])
def test_corrupt_checkpoint_cannot_be_claimed(pending, change):
    statements = {
        "status": "UPDATE research_checkpoints SET status='running'",
        "owner": "UPDATE research_checkpoints SET user_id=NULL",
        "missing_integrity": "DELETE FROM research_checkpoint_integrities",
    }
    with pending.engine.begin() as connection:
        connection.execute(text(statements[change]))
    with pytest.raises(ReviewClaimIntegrityError):
        pending.service.claim(SESSION, pending.users["one"])
    assert _claim_state(pending) is None


def test_changed_sealed_basis_blocks_live_claim_but_expiry_allows_new_basis(pending):
    one = pending.users["one"]
    old = pending.service.claim(SESSION, one)
    assert pending.checkpoints.save_checkpoint(SESSION, pending.state)
    for operation in (
        lambda: pending.service.accept(SESSION, one, old.token, APPROVE),
        lambda: pending.service.claim(SESSION, one),
        lambda: pending.service.renew(SESSION, one, old.token),
    ):
        with pytest.raises(ReviewClaimConflict):
            operation()
    _expire(pending)
    new = pending.service.claim(SESSION, pending.users["two"])
    assert new.basis_version > old.basis_version and new.token != old.token


@pytest.mark.parametrize("hook,committed", [("before_commit", False), ("after_commit", True)])
def test_commit_failure_is_not_success_and_retry_resolves_actual_database_state(pending, hook, committed):
    one = pending.users["one"]
    receipt = pending.service.claim(SESSION, one)

    def broken_factory():
        db = Session(pending.engine)

        def fail(_db):
            raise OperationalError("commit", {}, RuntimeError("sensitive connection diagnostic"))

        event.listen(db, hook, fail, once=True)
        return db

    failing = ReviewClaimService(session_factory=broken_factory, reviewer_policy=pending.policy)
    with pytest.raises(ReviewClaimUnavailable) as error:
        failing.accept(SESSION, one, receipt.token, APPROVE)
    assert "sensitive connection diagnostic" not in str(error.value)
    assert _claim_state(pending)["state"] == ("accepted" if committed else "claimed")
    resumed = pending.service.accept(SESSION, one, receipt.token, APPROVE)
    assert resumed.state == "accepted"
    assert pending.engine.pool.checkedout() == 0


def test_corrupted_accepted_decision_is_not_returned_as_an_idempotent_success(pending):
    one = pending.users["one"]
    receipt = pending.service.claim(SESSION, one)
    pending.service.accept(SESSION, one, receipt.token, APPROVE)
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE research_review_claims SET decision_digest=:digest"), {"digest": "0" * 64})
    with pytest.raises(ReviewClaimIntegrityError):
        pending.service.claim(SESSION, one)
    with pytest.raises(ReviewClaimIntegrityError):
        pending.service.accept(SESSION, one, receipt.token, APPROVE)


@pytest.mark.parametrize("field", ["token", "basis_seal"])
@pytest.mark.parametrize("expired", [False, True])
def test_malformed_persisted_claim_cannot_be_returned_or_auto_repaired(pending, field, expired):
    one = pending.users["one"]
    pending.service.claim(SESSION, one)
    bad = "é" * (43 if field == "token" else 64)
    with pending.engine.begin() as connection:
        connection.execute(text(f"UPDATE research_review_claims SET {field}=:bad"), {"bad": bad})
    if expired:
        _expire(pending)
    with pytest.raises(ReviewClaimIntegrityError):
        pending.service.claim(SESSION, one)
    assert _claim_state(pending)[field] == bad


def test_non_ascii_decision_digest_is_integrity_failure_not_database_outage(pending):
    one = pending.users["one"]
    receipt = pending.service.claim(SESSION, one)
    pending.service.accept(SESSION, one, receipt.token, APPROVE)
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE research_review_claims SET decision_digest=:bad"), {"bad": "é" * 64})
    with pytest.raises(ReviewClaimIntegrityError):
        pending.service.accept(SESSION, one, receipt.token, APPROVE)
