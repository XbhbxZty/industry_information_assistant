"""Durable, database-only ownership for a pending human review.

This service deliberately stops at recording an accepted decision.  Applying
that decision to the graph/checkpoint is a later, separately coordinated step;
mixing it into the lease transaction would make the ownership boundary much
harder to audit and recover.
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
    from service.checkpoint_integrity import CheckpointIntegrityError
    from service.checkpoint_service import CheckpointService
    from service.risk_scorecard import apply_human_review
except ImportError:  # pragma: no cover - supports package-style callers.
    from app.config.reviewer_policy import REVIEWER_POLICY, ReviewerPolicy
    from app.core.database import SessionLocal
    from app.models.research import (
        ResearchCheckpoint,
        ResearchCheckpointIntegrity,
        ResearchReviewClaim,
    )
    from app.models.user import User
    from app.service.checkpoint_integrity import CheckpointIntegrityError
    from app.service.checkpoint_service import CheckpointService
    from app.service.risk_scorecard import apply_human_review


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


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_USER_DECISION_KEYS = frozenset({"approved", "comment", "override_level"})
_SAVED_DECISION_KEYS = _USER_DECISION_KEYS | frozenset({"reviewer", "reviewer_id"})
_DB_CONFLICT_CODES = frozenset({"55P03", "40P01", "40001", "23505"})


class ReviewClaimService:
    """Claim/accept/release/renew pending review work in small PG transactions."""

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

            if claim is None:
                if supplied_token is not None:
                    raise ReviewClaimConflict("staletoken")
                claim = ResearchReviewClaim(
                    checkpoint_id=checkpoint.id,
                    owner_id=owner_id,
                    reviewer_id=reviewer.id,
                    token=secrets.token_urlsafe(32),
                    basis_version=integrity.business_revision,
                    basis_seal=integrity.business_seal,
                    state="claimed",
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                )
                db.add(claim)
                db.flush()
                return self._receipt(claim, checkpoint)

            if claim.state == "claimed":
                if self._is_live(claim.lease_expires_at, now):
                    self._verify_claim_basis(claim, checkpoint, integrity)
                    self._require_claim_identity(claim, reviewer.id, supplied_token)
                    return self._receipt(claim, checkpoint)
                if supplied_token is not None:
                    raise ReviewClaimConflict("expired")
                self._verify_claim_owner(claim, checkpoint)
                self._reclaim(claim, integrity, reviewer.id, now)
                db.flush()
                return self._receipt(claim, checkpoint)
            if claim.state == "released":
                if supplied_token is not None:
                    raise ReviewClaimConflict("staletoken")
                self._verify_claim_owner(claim, checkpoint)
                self._reclaim(claim, integrity, reviewer.id, now)
                db.flush()
                return self._receipt(claim, checkpoint)
            if claim.state == "accepted":
                self._verify_claim_basis(claim, checkpoint, integrity)
                self._verify_saved_decision(claim)
                self._require_claim_identity(claim, reviewer.id, supplied_token)
                return self._receipt(claim, checkpoint)
            if claim.state == "finalized":
                raise ReviewClaimConflict("finalized")
            raise ReviewClaimIntegrityError()

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

            if claim.state == "accepted":
                saved = self._verify_saved_decision(claim)
                if self._user_fields(saved) != normalized:
                    raise ReviewClaimConflict("decision")
                return self._receipt(claim, checkpoint)
            if claim.state != "claimed":
                raise ReviewClaimConflict("finalized" if claim.state == "finalized" else "conflict")
            if not self._is_live(claim.lease_expires_at, now):
                raise ReviewClaimConflict("expired")

            attributed_decision = {
                **normalized,
                "reviewer": reviewer.username,
                "reviewer_id": str(reviewer.id),
            }
            if not isinstance(reviewer.username, str) or not reviewer.username.strip():
                raise ReviewClaimForbidden("reviewer identity unavailable")
            assessment = checkpoint.state_json.get("risk_assessment")
            if not isinstance(assessment, dict):
                raise ReviewClaimIntegrityError()
            try:
                # This is validation only.  Persisting the revised graph state is
                # intentionally outside this ownership service.
                apply_human_review(
                    assessment,
                    attributed_decision,
                    scoring_view=checkpoint.state_json.get("scoring_view"),
                    field_checks=checkpoint.state_json.get("field_checks"),
                )
            except ValueError as exc:
                raise ReviewClaimInvalidDecision("invalid review decision") from exc

            claim.state = "accepted"
            claim.decision_json = copy.deepcopy(attributed_decision)
            claim.decision_digest = self._decision_digest(attributed_decision)
            claim.accepted_at = now
            db.flush()
            return self._receipt(claim, checkpoint)

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

    def _execute(self, operation: Callable[[Session], ReviewClaimReceipt]) -> ReviewClaimReceipt:
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
