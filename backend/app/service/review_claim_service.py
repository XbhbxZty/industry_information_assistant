"""Durable, database-only ownership for a pending human review.

Accepted decisions remain durable review ownership.  Their only completion
path is the short, atomic finalization transaction below, which applies the
deterministic state/report result and seals it with the terminal claim.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

try:  # The application is normally imported with ``backend/app`` on sys.path.
    from config.reviewer_policy import REVIEWER_POLICY, ReviewerPolicy
    from core.database import SessionLocal
    from models.research import (
        ResearchCheckpoint,
        ResearchCheckpointIntegrity,
        ResearchReviewClaim,
    )
    from models.user import User
    from service.checkpoint_integrity import (
        CheckpointIntegrityError,
        _state_projection,
        verify_graph_state_seal,
    )
    from service.checkpoint_service import CheckpointService
    from service.risk_scorecard import apply_human_review, render_markdown
except ImportError:  # pragma: no cover - supports package-style callers.
    from app.config.reviewer_policy import REVIEWER_POLICY, ReviewerPolicy
    from app.core.database import SessionLocal
    from app.models.research import (
        ResearchCheckpoint,
        ResearchCheckpointIntegrity,
        ResearchReviewClaim,
    )
    from app.models.user import User
    from app.service.checkpoint_integrity import (
        CheckpointIntegrityError,
        _state_projection,
        verify_graph_state_seal,
    )
    from app.service.checkpoint_service import CheckpointService
    from app.service.risk_scorecard import apply_human_review, render_markdown


class ReviewClaimError(RuntimeError):
    """Base class for review-claim failures safe to expose to a caller."""


class ReviewClaimConflict(ReviewClaimError):
    """A concurrent, stale, expired, or otherwise non-current operation."""

    def __init__(self, code: str = "conflict") -> None:
        self.code = code
        super().__init__(code)


class ReviewClaimForbidden(ReviewClaimError):
    """The supplied reviewer identity may not perform this operation."""


class ReviewClaimNotFound(ReviewClaimError):
    """No checkpoint exists for the requested session."""


class ReviewClaimInvalidDecision(ReviewClaimError):
    """The caller supplied a decision outside the stable review contract."""


class ReviewClaimIntegrityError(ReviewClaimConflict):
    """A sealed checkpoint or a persisted accepted decision is not trustworthy."""

    def __init__(self) -> None:
        super().__init__("integrity")


class ReviewClaimUnavailable(ReviewClaimError):
    """The database path failed; callers must not interpret this as no work."""


@dataclass(frozen=True)
class ReviewClaimReceipt:
    """Detached result object; it never retains an ORM session or row."""

    checkpoint_id: str
    session_id: str
    owner_id: str
    reviewer_id: str
    token: str = field(repr=False)
    basis_version: int
    basis_seal: str = field(repr=False)
    state: str
    lease_expires_at: datetime
    decision_digest: Optional[str]
    decision_json: Optional[Dict[str, Any]] = field(repr=False)
    accepted_at: Optional[datetime]
    finalized_at: Optional[datetime]


@dataclass(frozen=True)
class FinalizedReview:
    """Detached committed result of applying an accepted human decision."""

    claim: ReviewClaimReceipt
    state_json: Dict[str, Any] = field(repr=False)
    ui_state_json: Optional[Dict[str, Any]] = field(repr=False)
    final_report: str = field(repr=False)


@dataclass(frozen=True)
class ReviewSubmission:
    """Server-only accepted/finalized execution context, never an API payload."""

    claim: ReviewClaimReceipt
    state_json: Dict[str, Any] = field(repr=False)
    ui_state_json: Optional[Dict[str, Any]] = field(repr=False)


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_USER_DECISION_KEYS = frozenset({"approved", "comment", "override_level"})
_SAVED_DECISION_KEYS = _USER_DECISION_KEYS | frozenset({"reviewer", "reviewer_id"})
_DB_CONFLICT_CODES = frozenset({"55P03", "40P01", "40001", "23505"})


class ReviewClaimService:
    """Claim, accept, and atomically finalize pending review work in short PG transactions."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        reviewer_policy: ReviewerPolicy = REVIEWER_POLICY,
        lease_seconds: int = 300,
    ) -> None:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be an integer from 1 through 3600")
        self._session_factory = session_factory
        self._reviewer_policy = reviewer_policy
        self._lease_seconds = lease_seconds
        self._integrity_verifier = CheckpointService()

    def claim(
        self, session_id: object, reviewer_id: object, *, token: Optional[object] = None,
    ) -> ReviewClaimReceipt:
        requested_session = self._session_id(session_id)
        supplied_token = self._optional_token(token)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)

        def operation(db: Session) -> ReviewClaimReceipt:
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested_session)
            owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
            self._forbid_self_review(owner_id, reviewer.id)
            claim = self._claim_pending(db, checkpoint, integrity, claim, reviewer, now, supplied_token)
            return self._receipt(claim, checkpoint)

        return self._execute(operation)

    def accept(
        self, session_id: object, reviewer_id: object, token: object, decision: object,
    ) -> ReviewClaimReceipt:
        requested_session = self._session_id(session_id)
        supplied_token = self._required_token(token)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)
        normalized = self._normalize_user_decision(decision)

        def operation(db: Session) -> ReviewClaimReceipt:
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested_session)
            owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
            self._forbid_self_review(owner_id, reviewer.id)
            if claim is None:
                raise ReviewClaimConflict("staletoken")
            self._verify_claim_basis(claim, checkpoint, integrity)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            self._accept_pending(db, checkpoint, claim, reviewer, normalized, now)
            return self._receipt(claim, checkpoint)

        return self._execute(operation)

    def _claim_pending(self, db, checkpoint, integrity, claim, reviewer, now, supplied_token=None):
        """Shared locked claim operation for explicit claims and HTTP preparation."""
        if claim is None:
            if supplied_token is not None:
                raise ReviewClaimConflict("staletoken")
            claim = ResearchReviewClaim(
                checkpoint_id=checkpoint.id, owner_id=checkpoint.user_id, reviewer_id=reviewer.id,
                token=secrets.token_urlsafe(32), basis_version=integrity.business_revision,
                basis_seal=integrity.business_seal, state="claimed",
                lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            )
            db.add(claim)
        elif claim.state == "claimed" and self._is_live(claim.lease_expires_at, now):
            self._verify_claim_basis(claim, checkpoint, integrity)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            return claim
        elif claim.state in {"claimed", "released"}:
            if supplied_token is not None:
                raise ReviewClaimConflict("expired" if claim.state == "claimed" else "staletoken")
            self._verify_claim_owner(claim, checkpoint)
            self._reclaim(claim, integrity, reviewer.id, now)
        elif claim.state == "accepted":
            self._verify_claim_basis(claim, checkpoint, integrity)
            self._verify_saved_decision(claim)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            return claim
        elif claim.state == "finalized":
            raise ReviewClaimConflict("finalized")
        else:
            raise ReviewClaimIntegrityError()
        db.flush()
        return claim

    def _accept_pending(self, db, checkpoint, claim, reviewer, normalized, now):
        """Shared decision validation; caller owns the entire transaction."""
        if claim.state == "accepted":
            if self._user_fields(self._verify_saved_decision(claim)) != normalized:
                raise ReviewClaimConflict("decision")
            return
        if claim.state != "claimed":
            raise ReviewClaimConflict("finalized" if claim.state == "finalized" else "conflict")
        if not self._is_live(claim.lease_expires_at, now):
            raise ReviewClaimConflict("expired")
        attributed = {**normalized, "reviewer": reviewer.username, "reviewer_id": str(reviewer.id)}
        if not isinstance(reviewer.username, str) or not reviewer.username.strip():
            raise ReviewClaimForbidden("reviewer identity unavailable")
        assessment = checkpoint.state_json.get("risk_assessment")
        if not isinstance(assessment, dict):
            raise ReviewClaimIntegrityError()
        try:
            apply_human_review(
                assessment, attributed, scoring_view=checkpoint.state_json.get("scoring_view"),
                field_checks=checkpoint.state_json.get("field_checks"),
            )
        except ValueError as exc:
            raise ReviewClaimInvalidDecision("invalid review decision") from exc
        claim.state = "accepted"
        claim.decision_json = copy.deepcopy(attributed)
        claim.decision_digest = self._decision_digest(attributed)
        claim.accepted_at = now
        db.flush()

    def prepare_submission(self, session_id: object, reviewer_id: object, decision: object) -> ReviewSubmission:
        """Claim and accept before opening SSE; invalid decisions leave no claim.

        The existing session + authenticated reviewer + normalized decision is
        the retry identity. Tokens stay server-side; a retry cannot change an
        already accepted decision or take over another reviewer's accepted work.
        """
        requested = self._session_id(session_id)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)
        normalized = self._normalize_user_decision(decision)

        def operation(db):
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested)
            if claim is not None and claim.state == "finalized":
                self._authorize_finalized(checkpoint, integrity, claim, reviewer.id, requested)
                if self._user_fields(self._verify_saved_decision(claim)) != normalized:
                    raise ReviewClaimConflict("decision")
            else:
                owner = self._eligible_pending(checkpoint, integrity, requested)
                self._forbid_self_review(owner, reviewer.id)
                claim = self._claim_pending(db, checkpoint, integrity, claim, reviewer, now)
                self._accept_pending(db, checkpoint, claim, reviewer, normalized, now)
            return self._submission(checkpoint, claim)

        return self._execute(operation)

    def read_submission(
        self, session_id: object, reviewer_id: object, token: object, *, renew: bool = False,
    ) -> ReviewSubmission:
        """Recheck durable authority when the stream actually starts/resumes."""
        requested = self._session_id(session_id)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)
        supplied_token = self._required_token(token)

        def operation(db):
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested)
            if claim is not None and claim.state == "finalized":
                self._authorize_finalized(checkpoint, integrity, claim, reviewer.id, requested, supplied_token)
            else:
                owner = self._eligible_pending(checkpoint, integrity, requested)
                self._forbid_self_review(owner, reviewer.id)
                if claim is None or claim.state != "accepted":
                    raise ReviewClaimConflict("notaccepted")
                self._verify_claim_basis(claim, checkpoint, integrity)
                self._require_claim_identity(claim, reviewer.id, supplied_token)
                self._verify_saved_decision(claim)
                if renew:
                    claim.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
                    db.flush()
            return self._submission(checkpoint, claim)

        return self._execute(operation)

    def _authorize_finalized(self, checkpoint, integrity, claim, reviewer_id, session_id, token=None):
        self._verify_finalized_retry(checkpoint, integrity, claim, session_id)
        self._forbid_self_review(checkpoint.user_id, reviewer_id)
        self._verify_claim_owner(claim, checkpoint)
        self._require_claim_identity(claim, reviewer_id, token)

    def _submission(self, checkpoint, claim) -> ReviewSubmission:
        return ReviewSubmission(
            claim=self._receipt(claim, checkpoint), state_json=copy.deepcopy(checkpoint.state_json),
            ui_state_json=copy.deepcopy(checkpoint.ui_state_json),
        )

    def finalize(
        self,
        session_id: object,
        reviewer_id: object,
        token: object,
        final_state: object,
        *,
        ui_state: Optional[object] = None,
    ) -> FinalizedReview:
        """Atomically apply one accepted decision and complete its checkpoint.

        The graph state is an input to this boundary, not an authority: its
        graph seal, every unchanged business field, the deterministic risk
        result, and the report block are all checked before anything is made
        durable.  This keeps the accepted decision and the completed evidence
        record indivisible even if a client loses the commit response.
        """
        requested_session = self._session_id(session_id)
        supplied_token = self._required_token(token)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)

        def operation(db: Session) -> FinalizedReview:
            # Keep database role authorization before any target lookup, like
            # the other claim operations, so an untrusted identity cannot use
            # this endpoint to probe checkpoint existence or lock state.
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested_session)
            if claim is None:
                owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
                self._forbid_self_review(owner_id, reviewer.id)
                raise ReviewClaimConflict("staletoken")

            if claim.state == "finalized":
                # A completed checkpoint is no longer eligible_pending(), but
                # it still has to pass the same seal/owner checks *before*
                # identity comparison.  That makes self-review forbidden for
                # lost-response retries too, rather than a token conflict.
                try:
                    self._integrity_verifier._verify_integrity_pair(
                        checkpoint, integrity, requested_session,
                    )
                except CheckpointIntegrityError as exc:
                    raise ReviewClaimIntegrityError() from exc
                if checkpoint.user_id is None:
                    raise ReviewClaimIntegrityError()
                self._forbid_self_review(checkpoint.user_id, reviewer.id)
                self._verify_claim_owner(claim, checkpoint)
                self._require_claim_identity(claim, reviewer.id, supplied_token)
                self._verify_finalized_retry(checkpoint, integrity, claim, requested_session)
                return self._finalized_receipt(claim, checkpoint)

            owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
            self._forbid_self_review(owner_id, reviewer.id)
            self._verify_claim_owner(claim, checkpoint)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            self._verify_claim_basis(claim, checkpoint, integrity)
            if claim.state != "accepted":
                raise ReviewClaimConflict("conflict")
            decision = self._verify_saved_decision(claim)
            if not self._is_live(claim.lease_expires_at, now):
                raise ReviewClaimConflict("expired")

            completed_state, completed_report = self._validated_final_state(
                final_state, checkpoint, integrity, claim, decision, requested_session,
            )
            completed_ui = self._clean_final_ui_state(ui_state, checkpoint.ui_state_json)

            # No CheckpointService writer is called here: it owns a separate
            # transaction.  We already hold all rows in its documented order,
            # so the state, its versioned seals, and the terminal claim must
            # be changed and committed together.
            checkpoint.state_json = completed_state
            checkpoint.ui_state_json = completed_ui
            checkpoint.final_report = completed_report
            checkpoint.phase = "completed"
            checkpoint.status = "completed"
            checkpoint.updated_at = datetime.utcnow()
            self._integrity_verifier._refresh_integrity(
                checkpoint, integrity, integrity.business_revision + 1,
            )
            claim.state = "finalized"
            claim.finalized_at = now
            db.flush()
            return self._finalized_receipt(claim, checkpoint)

        return self._execute(operation)

    def release(
        self, session_id: object, reviewer_id: object, token: object,
    ) -> ReviewClaimReceipt:
        requested_session = self._session_id(session_id)
        supplied_token = self._required_token(token)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)

        def operation(db: Session) -> ReviewClaimReceipt:
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, _now = self._locked_context(db, requested_session)
            owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
            self._forbid_self_review(owner_id, reviewer.id)
            if claim is None:
                raise ReviewClaimConflict("staletoken")
            self._verify_claim_basis(claim, checkpoint, integrity)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            if claim.state == "released":
                return self._receipt(claim, checkpoint)
            if claim.state != "claimed":
                raise ReviewClaimConflict("finalized" if claim.state == "finalized" else "conflict")
            claim.state = "released"
            # The database shape requires these to remain empty for a release.
            claim.decision_digest = None
            claim.decision_json = None
            claim.accepted_at = None
            claim.finalized_at = None
            db.flush()
            return self._receipt(claim, checkpoint)

        return self._execute(operation)

    def renew(
        self, session_id: object, reviewer_id: object, token: object,
    ) -> ReviewClaimReceipt:
        requested_session = self._session_id(session_id)
        supplied_token = self._required_token(token)
        reviewer_uuid = self._reviewer_uuid(reviewer_id)

        def operation(db: Session) -> ReviewClaimReceipt:
            reviewer = self._authorized_reviewer(db, reviewer_uuid)
            checkpoint, integrity, claim, now = self._locked_context(db, requested_session)
            owner_id = self._eligible_pending(checkpoint, integrity, requested_session)
            self._forbid_self_review(owner_id, reviewer.id)
            if claim is None:
                raise ReviewClaimConflict("staletoken")
            self._verify_claim_basis(claim, checkpoint, integrity)
            self._require_claim_identity(claim, reviewer.id, supplied_token)
            if claim.state == "claimed":
                if not self._is_live(claim.lease_expires_at, now):
                    raise ReviewClaimConflict("expired")
            elif claim.state == "accepted":
                self._verify_saved_decision(claim)
            else:
                raise ReviewClaimConflict("finalized" if claim.state == "finalized" else "conflict")
            claim.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            db.flush()
            return self._receipt(claim, checkpoint)

        return self._execute(operation)

    def _validated_final_state(
        self,
        final_state: object,
        checkpoint: ResearchCheckpoint,
        integrity: ResearchCheckpointIntegrity,
        claim: ResearchReviewClaim,
        decision: Dict[str, Any],
        session_id: str,
    ) -> tuple[Dict[str, Any], str]:
        """Validate a graph-produced terminal state against the sealed basis."""
        if not isinstance(final_state, dict):
            raise ReviewClaimConflict("finalstate")
        candidate = dict(final_state)
        if candidate.get("session_id") != session_id or candidate.get("phase") != "completed":
            raise ReviewClaimConflict("finalstate")

        try:
            # This must happen before storage cleaning.  Otherwise a cleaner
            # could turn an attacker-provided graph value into a different,
            # apparently valid persisted object.
            verify_graph_state_seal(candidate, integrity.mode)
        except CheckpointIntegrityError as exc:
            raise ReviewClaimIntegrityError() from exc

        # Runtime queues/locks can be uncopyable. They are explicitly outside
        # the storage/signing contract, so validate the original first and
        # copy only persisted fields; the cleaned result is verified again.
        try:
            candidate = copy.deepcopy({
                key: value for key, value in candidate.items() if not key.startswith("_")
            })
        except Exception as exc:
            raise ReviewClaimConflict("finalstate") from exc

        pending = checkpoint.state_json
        pending_assessment = pending.get("risk_assessment") if isinstance(pending, dict) else None
        if not isinstance(pending_assessment, dict):
            raise ReviewClaimIntegrityError()
        try:
            expected_assessment = apply_human_review(
                copy.deepcopy(pending_assessment),
                copy.deepcopy(decision),
                scoring_view=copy.deepcopy(pending.get("scoring_view")),
                field_checks=copy.deepcopy(pending.get("field_checks")),
                reviewed_at=claim.accepted_at.isoformat(),
            )
        except (ValueError, TypeError, AttributeError) as exc:
            # The decision was accepted against this exact basis.  A failure
            # here therefore identifies persisted corruption, never a reason
            # to silently choose another final risk result.
            raise ReviewClaimIntegrityError() from exc

        candidate_assessment = candidate.get("risk_assessment")
        if not self._strict_json_equal(candidate_assessment, expected_assessment):
            raise ReviewClaimConflict("finalstate")

        try:
            pending_projection = _state_projection(pending)
            candidate_projection = _state_projection(candidate)
        except CheckpointIntegrityError as exc:
            raise ReviewClaimIntegrityError() from exc
        for projection in (pending_projection, candidate_projection):
            for field_name in ("risk_assessment", "final_report", "phase"):
                projection.pop(field_name, None)
        if not self._strict_json_equal(candidate_projection, pending_projection):
            raise ReviewClaimConflict("finalstate")

        pending_report = pending.get("final_report", "")
        if pending_report is None:
            pending_report = ""
        if not isinstance(pending_report, str):
            raise ReviewClaimIntegrityError()
        expected_report = self._canonical_final_report(pending_report, expected_assessment)
        if candidate.get("final_report") != expected_report:
            raise ReviewClaimConflict("finalstate")

        try:
            clean_state = self._integrity_verifier._clean_state_for_storage(candidate)
            # Runtime fields are intentionally absent from storage.  They are
            # outside graph signing, but the resulting persisted value must
            # still verify before it may receive a business/context seal.
            verify_graph_state_seal(clean_state, integrity.mode)
        except CheckpointIntegrityError as exc:
            raise ReviewClaimIntegrityError() from exc
        except Exception as exc:
            raise ReviewClaimConflict("finalstate") from exc
        return clean_state, expected_report

    def _clean_final_ui_state(
        self, ui_state: Optional[object], current_ui_state: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Preserve omitted UI state, or strictly clean the supplied snapshot."""
        if ui_state is None:
            try:
                return copy.deepcopy(current_ui_state)
            except Exception as exc:
                raise ReviewClaimIntegrityError() from exc
        if not isinstance(ui_state, dict):
            raise ReviewClaimConflict("finalstate")
        try:
            clean = self._integrity_verifier._clean_state_for_storage(copy.deepcopy(ui_state))
            # JSONB must not receive a Python-only value merely because the
            # ordinary checkpoint cleaner would stringify it recursively.
            json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            return clean
        except (TypeError, ValueError) as exc:
            raise ReviewClaimConflict("finalstate") from exc
        except Exception as exc:
            raise ReviewClaimUnavailable("review claim service unavailable") from exc

    @staticmethod
    def _strict_json_equal(left: object, right: object) -> bool:
        """Compare JSON values without Python's ``True == 1`` coercion."""
        try:
            return json.dumps(
                left, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ) == json.dumps(
                right, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _canonical_final_report(report: str, assessment: Dict[str, Any]) -> str:
        # Importing a graph agent at module import time creates a substantial
        # dependency cycle.  The writer already owns this report rule, so load
        # only that helper after the accepted decision and its basis are known.
        try:
            from service.deep_research_v2.agents.writer import _canonicalize_risk_block
        except ImportError:  # pragma: no cover - package-style callers.
            from app.service.deep_research_v2.agents.writer import _canonicalize_risk_block
        return _canonicalize_risk_block(report, render_markdown(assessment))

    def _verify_finalized_retry(
        self,
        checkpoint: ResearchCheckpoint,
        integrity: ResearchCheckpointIntegrity,
        claim: ResearchReviewClaim,
        session_id: str,
    ) -> None:
        """Fail closed before returning a lost-response finalization retry."""
        try:
            self._integrity_verifier._verify_integrity_pair(checkpoint, integrity, session_id)
        except CheckpointIntegrityError as exc:
            raise ReviewClaimIntegrityError() from exc
        decision = self._verify_saved_decision(claim)
        if (
            checkpoint.status != "completed"
            or checkpoint.phase != "completed"
            or not isinstance(claim.finalized_at, datetime)
            or claim.finalized_at.tzinfo is None
            or claim.finalized_at < claim.accepted_at
            or integrity.business_revision != claim.basis_version + 1
        ):
            raise ReviewClaimIntegrityError()
        state = checkpoint.state_json
        assessment = state.get("risk_assessment") if isinstance(state, dict) else None
        if (
            not isinstance(state, dict)
            or state.get("phase") != "completed"
            or not isinstance(assessment, dict)
        ):
            raise ReviewClaimIntegrityError()
        if (
            not isinstance(checkpoint.final_report, str)
            or not isinstance(state.get("final_report"), str)
            or checkpoint.final_report != state["final_report"]
        ):
            raise ReviewClaimIntegrityError()
        self._verify_final_human_review(assessment, decision, claim.accepted_at)

    @staticmethod
    def _verify_final_human_review(
        assessment: Dict[str, Any], decision: Dict[str, Any], accepted_at: datetime,
    ) -> None:
        review = assessment.get("human_review")
        if not isinstance(review, dict):
            raise ReviewClaimIntegrityError()
        expected = {
            "completed": True,
            "approved": decision["approved"],
            "reviewer": decision["reviewer"],
            "reviewer_id": decision["reviewer_id"],
            "comment": decision["comment"],
            "override_level": decision["override_level"],
            "reviewed_at": accepted_at.isoformat(),
            "engine_composite_score": assessment.get("composite_score"),
        }
        if any(not ReviewClaimService._strict_json_equal(review.get(key), value)
               for key, value in expected.items()):
            raise ReviewClaimIntegrityError()
        engine_level = review.get("engine_level")
        if not isinstance(engine_level, str) or not engine_level:
            raise ReviewClaimIntegrityError()
        if decision["approved"] is not True or decision["override_level"] is None:
            if not ReviewClaimService._strict_json_equal(engine_level, assessment.get("level")):
                raise ReviewClaimIntegrityError()
        elif not ReviewClaimService._strict_json_equal(
            assessment.get("level"), decision["override_level"],
        ):
            raise ReviewClaimIntegrityError()

    @staticmethod
    def _finalized_receipt(
        claim: ResearchReviewClaim, checkpoint: ResearchCheckpoint,
    ) -> FinalizedReview:
        report = checkpoint.final_report
        if not isinstance(report, str):
            raise ReviewClaimIntegrityError()
        return FinalizedReview(
            claim=ReviewClaimService._receipt(claim, checkpoint),
            state_json=copy.deepcopy(checkpoint.state_json),
            ui_state_json=copy.deepcopy(checkpoint.ui_state_json),
            final_report=copy.deepcopy(report),
        )

    def _execute(self, operation: Callable[[Session], Any]) -> Any:
        db: Optional[Session] = None
        try:
            db = self._session_factory()
            self._require_postgresql(db)
            receipt = operation(db)
            # Commit is deliberately inside the protected region: a failed
            # commit is never allowed to look like an issued receipt.
            db.commit()
            return receipt
        except ReviewClaimError:
            self._rollback(db)
            raise
        except DBAPIError as exc:
            self._rollback(db)
            raise self._database_error(exc) from exc
        except Exception as exc:
            self._rollback(db)
            raise ReviewClaimUnavailable("review claim service unavailable") from exc
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    @staticmethod
    def _rollback(db: Optional[Session]) -> None:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass

    @staticmethod
    def _require_postgresql(db: Session) -> None:
        try:
            if db.get_bind().dialect.name != "postgresql":
                raise ReviewClaimUnavailable("review claims require PostgreSQL")
        except ReviewClaimError:
            raise
        except Exception as exc:
            raise ReviewClaimUnavailable("review claim service unavailable") from exc

    @staticmethod
    def _database_error(exc: DBAPIError) -> ReviewClaimError:
        original = getattr(exc, "orig", None)
        code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
        if code in _DB_CONFLICT_CODES:
            return ReviewClaimConflict("locked" if code == "55P03" else "conflict")
        return ReviewClaimUnavailable("review claim service unavailable")

    @staticmethod
    def _session_id(value: object) -> str:
        if not isinstance(value, str):
            raise ReviewClaimNotFound("session not found")
        candidate = value.strip()
        if not candidate or len(candidate) > 64:
            raise ReviewClaimNotFound("session not found")
        return candidate

    @staticmethod
    def _reviewer_uuid(value: object) -> UUID:
        if not isinstance(value, (str, UUID)):
            raise ReviewClaimForbidden("reviewer is not authorized")
        try:
            parsed = UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ReviewClaimForbidden("reviewer is not authorized") from exc
        return parsed

    @staticmethod
    def _optional_token(value: Optional[object]) -> Optional[str]:
        if value is None:
            return None
        return ReviewClaimService._required_token(value)

    @staticmethod
    def _required_token(value: object) -> str:
        if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
            raise ReviewClaimConflict("staletoken")
        return value

    @staticmethod
    def _normalize_user_decision(value: object) -> Dict[str, Any]:
        if not isinstance(value, dict) or set(value) - _USER_DECISION_KEYS:
            raise ReviewClaimInvalidDecision("invalid review decision")
        if "approved" not in value or not isinstance(value["approved"], bool):
            raise ReviewClaimInvalidDecision("invalid review decision")
        comment = value.get("comment", "")
        if comment is None:
            comment = ""
        if not isinstance(comment, str):
            raise ReviewClaimInvalidDecision("invalid review decision")
        override = value.get("override_level")
        if override is not None and not isinstance(override, str):
            raise ReviewClaimInvalidDecision("invalid review decision")
        return {
            "approved": value["approved"],
            "comment": comment.strip(),
            "override_level": override.strip() or None if override is not None else None,
        }

    def _locked_context(
        self, db: Session, session_id: str,
    ) -> tuple[ResearchCheckpoint, ResearchCheckpointIntegrity, Optional[ResearchReviewClaim], datetime]:
        # This order must never change; otherwise a checker/finalizer can form
        # a deadlock cycle with this service.
        checkpoint = db.query(ResearchCheckpoint).filter(
            ResearchCheckpoint.session_id == session_id,
        ).with_for_update(nowait=True).one_or_none()
        if checkpoint is None:
            raise ReviewClaimNotFound("session not found")
        integrity = db.query(ResearchCheckpointIntegrity).filter(
            ResearchCheckpointIntegrity.session_id == session_id,
        ).with_for_update(nowait=True).one_or_none()
        if integrity is None:
            raise ReviewClaimIntegrityError()
        claim = db.query(ResearchReviewClaim).filter(
            ResearchReviewClaim.checkpoint_id == checkpoint.id,
        ).with_for_update(nowait=True).one_or_none()
        # PostgreSQL's transaction_timestamp()/now() is transaction-start time;
        # lease decisions need the actual time after all three locks are held.
        now = db.execute(text("SELECT clock_timestamp()")).scalar_one()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ReviewClaimUnavailable("review claim service unavailable")
        return checkpoint, integrity, claim, now

    def _authorized_reviewer(self, db: Session, reviewer_id: UUID) -> User:
        reviewer = db.query(User).filter(User.id == reviewer_id).one_or_none()
        if (
            reviewer is None
            or reviewer.is_active is not True
            or not self._reviewer_policy.can_review(
                reviewer.id, is_superuser=reviewer.is_superuser is True,
            )
        ):
            raise ReviewClaimForbidden("reviewer is not authorized")
        return reviewer

    def _eligible_pending(
        self,
        checkpoint: ResearchCheckpoint,
        integrity: ResearchCheckpointIntegrity,
        session_id: str,
    ) -> UUID:
        try:
            self._integrity_verifier._verify_integrity_pair(checkpoint, integrity, session_id)
        except CheckpointIntegrityError as exc:
            raise ReviewClaimIntegrityError() from exc
        except Exception as exc:
            raise ReviewClaimUnavailable("review claim service unavailable") from exc
        owner_id = checkpoint.user_id
        if owner_id is None:
            raise ReviewClaimForbidden("review task is not eligible")
        if checkpoint.status != "paused":
            raise ReviewClaimConflict("notpending")
        state = checkpoint.state_json
        assessment = state.get("risk_assessment") if isinstance(state, dict) else None
        review = assessment.get("human_review") if isinstance(assessment, dict) else None
        if (
            not isinstance(assessment, dict)
            or assessment.get("requires_human_review") is not True
            or (isinstance(review, dict) and review.get("completed") is True)
        ):
            raise ReviewClaimConflict("notpending")
        return owner_id

    @staticmethod
    def _is_live(expires_at: object, now: datetime) -> bool:
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
            raise ReviewClaimIntegrityError()
        return expires_at > now

    @staticmethod
    def _verify_claim_basis(
        claim: ResearchReviewClaim,
        checkpoint: ResearchCheckpoint,
        integrity: ResearchCheckpointIntegrity,
    ) -> None:
        ReviewClaimService._verify_claim_owner(claim, checkpoint)
        if (
            claim.basis_version != integrity.business_revision
            or not hmac.compare_digest(claim.basis_seal, integrity.business_seal)
        ):
            raise ReviewClaimConflict("basis_drift")

    @staticmethod
    def _verify_claim_owner(
        claim: ResearchReviewClaim, checkpoint: ResearchCheckpoint,
    ) -> None:
        # A valid but superseded basis may be rebound after expiry. A malformed
        # persisted claim is corruption, not an invitation to repair it by use.
        if (
            not isinstance(claim.token, str) or not _TOKEN_RE.fullmatch(claim.token)
            or type(claim.basis_version) is not int or claim.basis_version < 1
            or not isinstance(claim.basis_seal, str) or not _DIGEST_RE.fullmatch(claim.basis_seal)
        ):
            raise ReviewClaimIntegrityError()
        if (
            checkpoint.user_id is None
            or claim.owner_id != checkpoint.user_id
        ):
            raise ReviewClaimConflict("basis_drift")

    @staticmethod
    def _forbid_self_review(owner_id: UUID, reviewer_id: UUID) -> None:
        if owner_id == reviewer_id:
            raise ReviewClaimForbidden("reviewer is not authorized")

    @staticmethod
    def _require_claim_identity(
        claim: ResearchReviewClaim, reviewer_id: UUID, token: Optional[str],
    ) -> None:
        if claim.reviewer_id != reviewer_id:
            raise ReviewClaimConflict("conflict")
        if token is not None and not hmac.compare_digest(claim.token, token):
            raise ReviewClaimConflict("staletoken")

    def _reclaim(
        self,
        claim: ResearchReviewClaim,
        integrity: ResearchCheckpointIntegrity,
        reviewer_id: UUID,
        now: datetime,
    ) -> None:
        claim.reviewer_id = reviewer_id
        claim.token = secrets.token_urlsafe(32)
        claim.basis_version = integrity.business_revision
        claim.basis_seal = integrity.business_seal
        claim.state = "claimed"
        claim.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        claim.decision_digest = None
        claim.decision_json = None
        claim.accepted_at = None
        claim.finalized_at = None

    @staticmethod
    def _decision_digest(decision: Dict[str, Any]) -> str:
        try:
            payload = json.dumps(
                decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ReviewClaimInvalidDecision("invalid review decision") from exc
        return hashlib.sha256(payload).hexdigest()

    def _verify_saved_decision(self, claim: ResearchReviewClaim) -> Dict[str, Any]:
        saved = claim.decision_json
        if not isinstance(saved, dict) or set(saved) != _SAVED_DECISION_KEYS:
            raise ReviewClaimIntegrityError()
        try:
            user_fields = self._normalize_user_decision({
                key: saved[key] for key in _USER_DECISION_KEYS if key in saved
            })
            expected_digest = self._decision_digest(saved)
        except ReviewClaimInvalidDecision as exc:
            raise ReviewClaimIntegrityError() from exc
        if (
            user_fields["comment"] != saved.get("comment")
            or user_fields["override_level"] != saved.get("override_level")
            or not isinstance(saved.get("reviewer"), str)
            or not saved["reviewer"].strip()
            or saved.get("reviewer_id") != str(claim.reviewer_id)
            or not isinstance(claim.decision_digest, str)
            or not _DIGEST_RE.fullmatch(claim.decision_digest)
            or not hmac.compare_digest(
                claim.decision_digest, expected_digest,
            )
            or not isinstance(claim.accepted_at, datetime)
            or claim.accepted_at.tzinfo is None
        ):
            raise ReviewClaimIntegrityError()
        return saved

    @staticmethod
    def _user_fields(decision: Dict[str, Any]) -> Dict[str, Any]:
        return {key: decision[key] for key in _USER_DECISION_KEYS}

    @staticmethod
    def _receipt(
        claim: ResearchReviewClaim, checkpoint: ResearchCheckpoint,
    ) -> ReviewClaimReceipt:
        return ReviewClaimReceipt(
            checkpoint_id=str(checkpoint.id),
            session_id=checkpoint.session_id,
            owner_id=str(claim.owner_id),
            reviewer_id=str(claim.reviewer_id),
            token=claim.token,
            basis_version=claim.basis_version,
            basis_seal=claim.basis_seal,
            state=claim.state,
            lease_expires_at=claim.lease_expires_at,
            decision_digest=claim.decision_digest,
            decision_json=copy.deepcopy(claim.decision_json),
            accepted_at=claim.accepted_at,
            finalized_at=claim.finalized_at,
        )
