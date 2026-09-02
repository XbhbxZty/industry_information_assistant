"""Atomic finalization and retry invariants; no HTTP/SSE claims in this suite."""
from concurrent.futures import ThreadPoolExecutor
import copy
from threading import Barrier, Lock

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from test_review_claim_service_postgres import (
    pending, context_db, keys, disposable_postgres_database,  # noqa: F401
    SESSION, APPROVE, _basis, _claim_state, _expire,
)
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity
from service.checkpoint_integrity import (
    GRAPH_SEAL_FIELD, MODE_STANDARD, MODE_MANAGED, issue_graph_state_seal,
)
from service.deep_research_v2.agents.writer import _canonicalize_risk_block
from service.risk_scorecard import apply_human_review, render_markdown
from service.review_claim_service import (
    ReviewClaimService, ReviewClaimConflict, ReviewClaimForbidden,
    ReviewClaimIntegrityError, ReviewClaimUnavailable,
)


pytestmark = pytest.mark.postgres_integration


def _prepare(pending, *, decision=None, mode=MODE_STANDARD):
    state = copy.deepcopy(pending.state)
    state.update({
        "final_report": "# 尽调报告\n\n保留的原始证据正文。",
        "evidence_store": {"original-evidence": {"text": "交易材料原文", "source": "company"}},
        "company_profile": {"name": "测试企业", "financial": {"revenue": 100}},
    })
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, mode)
    if mode != MODE_STANDARD:
        # The fixture has no claim yet. Recreate its disposable target in the
        # requested mode instead of pretending mode drift is a legitimate save.
        assert pending.checkpoints.delete_checkpoint(SESSION)
    assert pending.checkpoints.save_checkpoint(
        SESSION, state, user_id=str(pending.users["owner"]),
        ui_state={"research_steps": [{"type": "review", "status": "completed"}]},
        final_report=state["final_report"],
    )
    assert pending.checkpoints.update_status(SESSION, "paused")
    receipt = pending.service.claim(SESSION, pending.users["one"])
    receipt = pending.service.accept(SESSION, pending.users["one"], receipt.token, decision or APPROVE)
    state["risk_assessment"] = apply_human_review(
        state["risk_assessment"], receipt.decision_json,
        scoring_view=state.get("scoring_view"), field_checks=state.get("field_checks"),
        reviewed_at=receipt.accepted_at.isoformat(),
    )
    state["final_report"] = _canonicalize_risk_block(
        state["final_report"], render_markdown(state["risk_assessment"]),
    )
    state["phase"] = "completed"
    state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, mode)
    return receipt, state


def _finalize(pending, receipt, state, **kwargs):
    return pending.service.finalize(SESSION, pending.users["one"], receipt.token, state, **kwargs)


@pytest.mark.parametrize("mode", [MODE_STANDARD, MODE_MANAGED])
@pytest.mark.parametrize("decision", [
    APPROVE,
    {"approved": False, "comment": "交易未确权"},
    {"approved": True, "comment": "补充审查理由", "override_level": "高风险"},
])
def test_finalization_commits_complete_state_and_original_decision_once(pending, mode, decision):
    receipt, state = _prepare(pending, decision=decision, mode=mode)
    before, claim_before = _basis(pending), _claim_state(pending)
    result = _finalize(pending, receipt, state, ui_state={"research_steps": []})
    assert result.claim.state == "finalized"
    assert result.claim.accepted_at == receipt.accepted_at
    assert result.claim.finalized_at >= receipt.accepted_at
    assert result.state_json == state and result.final_report == state["final_report"]
    assert result.ui_state_json == {"research_steps": []}
    stored = pending.checkpoints.load_full_checkpoint(SESSION)
    assert stored["status"] == "completed" and stored["phase"] == "completed"
    assert stored["state_json"] == result.state_json
    assert stored["final_report"] == result.final_report
    assert stored["ui_state_json"] == result.ui_state_json
    after = _basis(pending)
    assert after[4] == before[4] + 1
    assert after[5] != before[5] and after[6] != before[6]
    claim_after = _claim_state(pending)
    for field in ("checkpoint_id", "owner_id", "reviewer_id", "token", "basis_version",
                  "basis_seal", "decision_json", "decision_digest", "accepted_at", "created_at"):
        assert claim_after[field] == claim_before[field]
    assert result.state_json["risk_assessment"]["human_review"]["approved"] is decision["approved"]
    assert pending.engine.pool.checkedout() == 0
    for field in ("token", "basis_seal"):
        assert getattr(receipt, field) not in repr(result)
    # Results are copies, not mutation handles to persisted content.
    result.state_json["risk_assessment"]["level"] = "篡改"
    result.claim.decision_json["comment"] = "篡改"
    assert _basis(pending) == after and _claim_state(pending) == claim_after


def test_finalized_retry_returns_original_result_even_after_lease_expiry(pending):
    receipt, state = _prepare(pending)
    # Finalized records are frozen. Set a near lease while still accepted, then
    # simulate a later DB clock in the retry context without mutating that row.
    result = _finalize(pending, receipt, state)
    basis, claim = _basis(pending), _claim_state(pending)
    original_context = pending.service._locked_context

    def future_context(db, session_id):
        from datetime import timedelta
        cp, integrity, held, _now = original_context(db, session_id)
        return cp, integrity, held, held.lease_expires_at + timedelta(days=1)

    pending.service._locked_context = future_context
    retry = _finalize(pending, receipt, {"not": "a new report"}, ui_state={"forged": True})
    assert retry == result
    assert _basis(pending) == basis and _claim_state(pending) == claim
    assert not pending.checkpoints.save_checkpoint(SESSION, state)
    assert not pending.checkpoints.update_status(SESSION, "failed")


@pytest.mark.parametrize("damage", [
    "unsigned", "mode", "session", "phase", "evidence", "extra_field", "boolean_iteration",
    "assessment", "decision", "review_time", "report", "unreviewed_report",
])
def test_invalid_or_wrong_basis_graph_output_cannot_finalize(pending, damage):
    receipt, state = _prepare(pending)
    before, claim = _basis(pending), _claim_state(pending)
    if damage == "unsigned":
        state.pop(GRAPH_SEAL_FIELD)
    elif damage == "mode":
        state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_MANAGED)
    else:
        if damage == "session": state["session_id"] = "other"
        elif damage == "phase": state["phase"] = "reviewing"
        elif damage == "evidence": state["evidence_store"]["original-evidence"]["text"] = "其他材料"
        elif damage == "extra_field": state["new_business_field"] = "not part of review"
        elif damage == "boolean_iteration": state["iteration"] = True  # True == 1 must not pass.
        elif damage == "assessment": state["risk_assessment"]["level"] = "低风险"
        elif damage == "decision": state["risk_assessment"]["human_review"]["approved"] = False
        elif damage == "review_time": state["risk_assessment"]["human_review"]["reviewed_at"] = "forged"
        elif damage == "report": state["final_report"] += "\n\n与本次材料无关的新结论。"
        elif damage == "unreviewed_report": state["final_report"] = "# 尽调报告\n\n保留的原始证据正文。"
        state[GRAPH_SEAL_FIELD] = issue_graph_state_seal(state, MODE_STANDARD)
    with pytest.raises(ReviewClaimConflict):
        _finalize(pending, receipt, state)
    assert _basis(pending) == before and _claim_state(pending) == claim


@pytest.mark.parametrize("runtime", [object(), Lock()], ids=["plain", "uncopyable_lock"])
def test_runtime_fields_are_not_persisted_and_omitted_ui_preserves_previous_ui(pending, runtime):
    receipt, state = _prepare(pending)
    previous_ui = pending.checkpoints.load_full_checkpoint(SESSION)["ui_state_json"]
    state["_message_queue"] = runtime
    result = _finalize(pending, receipt, state)
    assert "_message_queue" not in result.state_json
    assert result.ui_state_json == previous_ui


def test_expired_accepted_claim_requires_renewal_before_finalization(pending):
    receipt, state = _prepare(pending)
    _expire(pending)
    before = _basis(pending)
    with pytest.raises(ReviewClaimConflict):
        _finalize(pending, receipt, state)
    assert _basis(pending) == before and _claim_state(pending)["state"] == "accepted"
    pending.service.renew(SESSION, pending.users["one"], receipt.token)
    assert _finalize(pending, receipt, state).claim.state == "finalized"


def test_claim_without_accepted_decision_cannot_finalize(pending):
    receipt = pending.service.claim(SESSION, pending.users["one"])
    with pytest.raises(ReviewClaimConflict):
        _finalize(pending, receipt, pending.state)
    assert _claim_state(pending)["state"] == "claimed"


def test_even_validly_resealed_basis_drift_cannot_finalize_old_output(pending):
    receipt, state = _prepare(pending)
    with Session(pending.engine) as db:
        checkpoint = db.query(ResearchCheckpoint).with_for_update().one()
        integrity = db.query(ResearchCheckpointIntegrity).with_for_update().one()
        pending.checkpoints._refresh_integrity(checkpoint, integrity, integrity.business_revision + 1)
        db.commit()
    before, claim = _basis(pending), _claim_state(pending)
    with pytest.raises(ReviewClaimConflict) as error:
        _finalize(pending, receipt, state)
    assert error.value.code == "basis_drift"
    assert _basis(pending) == before and _claim_state(pending) == claim


@pytest.mark.parametrize("after_completion", [False, True])
def test_finalization_rechecks_reviewer_and_token_even_for_retry(pending, after_completion):
    receipt, state = _prepare(pending)
    if after_completion:
        _finalize(pending, receipt, state)
    before, claim = _basis(pending), _claim_state(pending)
    for name in ("owner", "outsider", "disabled"):
        with pytest.raises(ReviewClaimForbidden):
            pending.service.finalize(SESSION, pending.users[name], receipt.token, state)
    with pytest.raises(ReviewClaimConflict):
        pending.service.finalize(SESSION, pending.users["two"], receipt.token, state)
    with pytest.raises(ReviewClaimConflict):
        pending.service.finalize(SESSION, pending.users["one"], "x" * 43, state)
    assert _basis(pending) == before and _claim_state(pending) == claim


@pytest.mark.parametrize("hook,committed", [("after_flush", False), ("before_commit", False), ("after_commit", True)])
def test_finalization_commit_failure_rolls_back_or_recovers_same_result(pending, hook, committed):
    receipt, state = _prepare(pending)
    before = _basis(pending)

    def factory():
        db = Session(pending.engine)
        def fail(*_args):
            raise OperationalError("commit", {}, RuntimeError("private database error"))
        event.listen(db, hook, fail, once=True)
        return db

    service = ReviewClaimService(session_factory=factory, reviewer_policy=pending.policy)
    with pytest.raises(ReviewClaimUnavailable) as error:
        service.finalize(SESSION, pending.users["one"], receipt.token, state)
    assert "private database error" not in str(error.value)
    assert _claim_state(pending)["state"] == ("finalized" if committed else "accepted")
    if not committed:
        assert _basis(pending) == before
    result = _finalize(pending, receipt, state)
    assert result.claim.state == "finalized" and _basis(pending)[4] == before[4] + 1
    assert pending.engine.pool.checkedout() == 0


def test_two_finalizers_only_commit_once_and_loser_can_retry(pending):
    receipt, state = _prepare(pending)
    before = _basis(pending)
    gate = Barrier(2)

    def finalize():
        service = ReviewClaimService(session_factory=lambda: Session(pending.engine), reviewer_policy=pending.policy)
        gate.wait(timeout=10)
        try:
            return service.finalize(SESSION, pending.users["one"], receipt.token, state)
        except ReviewClaimConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in (pool.submit(finalize), pool.submit(finalize))]
    completed = [result for result in results if result is not None]
    assert completed and all(result == completed[0] for result in completed)
    assert _basis(pending)[4] == before[4] + 1
    assert _finalize(pending, receipt, state) == completed[0]


@pytest.mark.parametrize("statement", [
    "UPDATE research_review_claims SET lease_expires_at=clock_timestamp()+interval '1 hour'",
    "UPDATE research_review_claims SET state='accepted', finalized_at=NULL",
    "UPDATE research_review_claims SET finalized_at=finalized_at+interval '1 second'",
    "DELETE FROM research_review_claims",
])
def test_finalized_decision_record_cannot_be_reopened_or_deleted(pending, statement):
    receipt, state = _prepare(pending)
    _finalize(pending, receipt, state)
    before = _claim_state(pending)
    with pytest.raises(IntegrityError):
        with pending.engine.begin() as connection:
            connection.execute(text(statement))
    assert _claim_state(pending) == before


def test_finalized_retry_rejects_corrupt_persisted_completion(pending):
    receipt, state = _prepare(pending)
    _finalize(pending, receipt, state)
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE research_checkpoints SET status='paused'"))
    with pytest.raises(ReviewClaimIntegrityError):
        _finalize(pending, receipt, state)


def test_finalized_retry_never_returns_tampered_report_column(pending):
    receipt, state = _prepare(pending)
    _finalize(pending, receipt, state)
    with pending.engine.begin() as connection:
        connection.execute(text("UPDATE research_checkpoints SET final_report='unsigned replacement report'"))
    with pytest.raises(ReviewClaimIntegrityError):
        _finalize(pending, receipt, state)
