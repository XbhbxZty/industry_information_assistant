"""D4b2a ownership guards on disposable PostgreSQL, not HTTP integration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from alembic import command
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_review_claim_service_postgres import (
    pending, context_db, keys, disposable_postgres_database,  # noqa: F401
    SESSION, APPROVE, _basis, _claim_state, _expire,
)
from test_legacy_schema_preflight_postgres import _alembic_config
from service.review_claim_service import ReviewClaimConflict


pytestmark = pytest.mark.postgres_integration


@pytest.mark.parametrize("state", ["claimed", "accepted", "accepted_expired"])
def test_normal_writes_cannot_bypass_effective_review_claim(pending, state):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    if state.startswith("accepted"):
        pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)
    if state == "accepted_expired":
        _expire(pending)
    before, claim = _basis(pending), _claim_state(pending)
    assert not pending.checkpoints.save_checkpoint(SESSION, pending.state)
    for status in ("running", "paused", "failed", "completed"):
        assert not pending.checkpoints.update_status(SESSION, status)
    assert not pending.checkpoints.delete_checkpoint(SESSION)
    assert _basis(pending) == before and _claim_state(pending) == claim
    assert pending.engine.pool.checkedout() == 0


@pytest.mark.parametrize("state", ["expired", "released"])
def test_unaccepted_inactive_claim_allows_new_basis_without_rewriting_claim(pending, state):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    if state == "expired":
        _expire(pending)
    else:
        pending.service.release(SESSION, pending.users["one"], receipt.token)
    claim = _claim_state(pending)
    assert pending.checkpoints.save_checkpoint(SESSION, pending.state)
    assert _claim_state(pending) == claim
    replacement = pending.service.claim(SESSION, pending.users["two"])
    assert replacement.basis_version == receipt.basis_version + 1
    assert replacement.token != receipt.token


def test_normal_save_racing_first_claim_never_changes_an_issued_basis(pending):
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=10)
        try:
            return pending.service.claim(SESSION, pending.users["one"])
        except ReviewClaimConflict:
            return None

    def save():
        gate.wait(timeout=10)
        return pending.checkpoints.save_checkpoint(SESSION, pending.state)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(claim), pool.submit(save)
        receipt, saved = first.result(timeout=15), second.result(timeout=15)
    assert receipt is not None or saved  # No deadlock/no double failure.
    if receipt is None:
        receipt = pending.service.claim(SESSION, pending.users["one"])
    assert receipt.basis_version == _basis(pending)[4]
    assert receipt.basis_seal == _basis(pending)[5]
    assert not pending.checkpoints.save_checkpoint(SESSION, pending.state)


@pytest.mark.parametrize("statement", [
    "UPDATE research_review_claims SET decision_digest=repeat('0',64)",
    "UPDATE research_review_claims SET decision_json=decision_json || jsonb_build_object('approved',false)",
    "UPDATE research_review_claims SET reviewer_id=owner_id, owner_id=reviewer_id",
    "UPDATE research_review_claims SET token=repeat('b',43)",
    "UPDATE research_review_claims SET basis_version=basis_version+1",
    "UPDATE research_review_claims SET basis_seal=repeat('0',64)",
    "UPDATE research_review_claims SET accepted_at=accepted_at+interval '1 second'",
    "UPDATE research_review_claims SET created_at=created_at+interval '1 second'",
    "UPDATE research_review_claims SET state='released', decision_digest=NULL, decision_json=NULL, accepted_at=NULL",
    "DELETE FROM research_review_claims",
    "TRUNCATE research_review_claims",
])
def test_database_rejects_mutation_or_deletion_of_accepted_decision(pending, statement):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)
    before = _claim_state(pending)
    with pytest.raises(IntegrityError, match="accepted review decision"):
        with pending.engine.begin() as connection:
            connection.execute(text(statement))
    assert _claim_state(pending) == before
    # Lease renewal is intentionally still permitted for recovery by the owner.
    renewed = pending.service.renew(SESSION, pending.users["one"], receipt.token)
    assert renewed.state == "accepted" and renewed.decision_json == before["decision_json"]


def test_guard_migration_downgrade_and_upgrade_preserve_decision(pending, disposable_postgres_database):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)
    before = _claim_state(pending)
    config = _alembic_config(disposable_postgres_database)
    command.check(config)
    command.downgrade(config, "20260902_0003")
    assert _claim_state(pending) == before
    with pending.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname='research_review_claims_accepted_immutable'")).scalar_one() == 0
    command.upgrade(config, "head")
    command.check(config)
    assert _claim_state(pending) == before
    with pytest.raises(IntegrityError):
        with pending.engine.begin() as connection:
            connection.execute(text("DELETE FROM research_review_claims"))


def test_raw_finalization_without_completed_checkpoint_is_rejected_at_commit(pending):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    pending.service.accept(SESSION, pending.users["one"], receipt.token, APPROVE)
    before, claim = _basis(pending), _claim_state(pending)
    with pytest.raises(IntegrityError, match="requires its completed checkpoint"):
        with pending.engine.begin() as connection:
            connection.execute(text("UPDATE research_review_claims SET state='finalized', finalized_at=clock_timestamp()"))
            # The check is deferred: a proper finalizer is allowed to flush
            # related rows in any order before the one transaction commits.
            assert connection.execute(text("SELECT state FROM research_review_claims")).scalar_one() == "finalized"
    assert _basis(pending) == before and _claim_state(pending) == claim
