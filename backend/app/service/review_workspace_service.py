"""Strict, least-privilege reader for pending human-review work.

The generic checkpoint service is intentionally forgiving for legacy UI flows:
some operational failures become ``None`` or ``[]`` there.  That behavior is
unsafe for a review queue because it could conceal a damaged pending case.
This service consequently reads through the request DB session and has a
separate error vocabulary; callers must never translate a service failure into
an empty queue or a 404.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity
from schemas.review_workspace import (
    ReviewCompleteness,
    ReviewCreditRecommendation,
    ReviewCriticalIssue,
    ReviewEvidenceSummary,
    ReviewProfileRef,
    ReviewRiskAssessment,
    ReviewTaskPacket,
    ReviewTaskSummary,
)
from service.checkpoint_integrity import CheckpointIntegrityError
from service.checkpoint_service import CheckpointService


class ReviewWorkspaceError(RuntimeError):
    """Base class for strict workspace failures."""


class ReviewTaskNotFound(ReviewWorkspaceError):
    """No checkpoint exists for the requested session."""


class ReviewTaskNotPending(ReviewWorkspaceError):
    """The checkpoint exists but is not an eligible pending review."""


class ReviewWorkspaceIntegrityError(ReviewWorkspaceError):
    """A sealed review candidate cannot be safely interpreted."""


class ReviewWorkspaceUnavailable(ReviewWorkspaceError):
    """The database/read path failed; never represent this as an empty queue."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_string(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReviewWorkspaceIntegrityError(f"复核状态字段 {field} 非法")
    return value


def _string_list(value: Any, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReviewWorkspaceIntegrityError(f"复核状态字段 {field} 必须是字符串列表")
    return list(value)


def _optional_number(value: Any, field: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewWorkspaceIntegrityError(f"复核状态字段 {field} 必须是数值")
    return float(value)


def _optional_int(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    if not _is_int(value):
        raise ReviewWorkspaceIntegrityError(f"复核状态字段 {field} 必须是整数")
    return value


def _triggered_rule_summaries(value: Any) -> List[str]:
    """Project scorecard rules to their human-readable business explanation.

    The live scorecard stores dictionaries (including internal evidence links),
    while a few older fixtures stored strings.  The reviewer contract exposes
    neither shape directly: it deliberately keeps only the readable ``detail``
    text and drops scores, field internals, and evidence identifiers.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReviewWorkspaceIntegrityError(
            "复核状态字段 risk_assessment.triggered_rules 必须是列表"
        )
    result: List[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
            continue
        if not isinstance(item, dict) or not isinstance(item.get("detail"), str):
            raise ReviewWorkspaceIntegrityError("复核状态评分规则摘要非法")
        result.append(item["detail"])
    return result


class ReviewWorkspaceService:
    """Build explicit review packets from integrity-verified business state."""

    def __init__(self, db: Session):
        self.db = db
        # Reuse only the audit-tested integrity primitive.  Database access is
        # still performed here so generic service exception swallowing cannot
        # turn an operational failure into a harmless-looking empty result.
        self._integrity_verifier = CheckpointService()

    def list_pending(self, reviewer_id: object, *, limit: int = 100) -> List[ReviewTaskSummary]:
        if not _is_int(limit) or limit < 1 or limit > 100:
            raise ValueError("limit 必须介于 1 和 100 之间")
        try:
            # Do not pre-filter by the mutable row status.  A sealed pending
            # task whose status was changed to ``completed``/``failed`` must be
            # reported as an integrity conflict, not disappear as an empty
            # queue.  We first make a permissive candidate selection below and
            # then authenticate every candidate before interpreting it.
            rows = self.db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.user_id.isnot(None),
            ).order_by(ResearchCheckpoint.updated_at.desc()).all()
        except Exception as exc:
            raise ReviewWorkspaceUnavailable("无法读取待复核队列") from exc

        tasks: List[ReviewTaskSummary] = []
        for checkpoint in rows:
            if str(checkpoint.user_id) == str(reviewer_id):
                continue
            state_hint = checkpoint.state_json
            assessment_hint = (
                state_hint.get("risk_assessment")
                if isinstance(state_hint, dict) else None
            )
            review_hint = (
                assessment_hint.get("human_review")
                if isinstance(assessment_hint, dict) else None
            )
            sealed_state_looks_pending = (
                isinstance(assessment_hint, dict)
                and assessment_hint.get("requires_human_review") is True
                and not (
                    isinstance(review_hint, dict)
                    and review_hint.get("completed") is True
                )
            )
            if checkpoint.status != "paused" and not sealed_state_looks_pending:
                continue
            try:
                packet = self._packet_for_checkpoint(checkpoint, require_pending=True)
            except ReviewTaskNotPending:
                # A mutable row may still say ``paused`` after the sealed
                # business state no longer requests review.  That is a normal
                # stale-candidate condition, not a reason to publish it.
                continue
            if len(tasks) < limit:
                tasks.append(self._summary(packet))
        return tasks

    def get_pending(self, session_id: str, reviewer_id: object) -> ReviewTaskPacket:
        _checkpoint, packet = self._load_pending(session_id, reviewer_id)
        return packet

    def authorize_submission(self, session_id: str, reviewer_id: object) -> str:
        """Verify the same strict boundary before a review decision is resumed.

        Returning the owner here keeps the submit route away from the generic,
        legacy-compatible checkpoint reader.  Atomic reviewer claiming remains
        a separate 3.4D concern, but a direct POST can no longer bypass this
        phase's integrity and pending-state checks.
        """
        checkpoint, _packet = self._load_pending(session_id, reviewer_id)
        return str(checkpoint.user_id)

    def _load_pending(
        self, session_id: str, reviewer_id: object,
    ) -> Tuple[ResearchCheckpoint, ReviewTaskPacket]:
        requested = (session_id or "").strip()
        if not requested:
            raise ReviewTaskNotFound("会话不存在")
        try:
            rows = self.db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == requested,
            ).order_by(ResearchCheckpoint.updated_at.desc()).all()
        except Exception as exc:
            raise ReviewWorkspaceUnavailable("无法读取复核任务") from exc
        if not rows:
            raise ReviewTaskNotFound("会话不存在")
        if len(rows) != 1:
            raise ReviewWorkspaceIntegrityError("同一 session_id 对应多个检查点")
        checkpoint = rows[0]
        if checkpoint.user_id is None:
            raise ReviewTaskNotPending("无归属会话不能进入复核队列")
        if str(checkpoint.user_id) == str(reviewer_id):
            raise ReviewTaskNotPending("研究发起人不得查看自己的复核任务")
        # Do not report an unsealed/mismatched row as a missing task.  The
        # precise status is checked after integrity, from business state.
        return checkpoint, self._packet_for_checkpoint(checkpoint, require_pending=True)

    def _packet_for_checkpoint(
        self, checkpoint: ResearchCheckpoint, *, require_pending: bool,
    ) -> ReviewTaskPacket:
        session_id = checkpoint.session_id
        if not isinstance(session_id, str) or not session_id:
            raise ReviewWorkspaceIntegrityError("检查点缺少合法 session_id")
        try:
            integrity = self.db.query(ResearchCheckpointIntegrity).filter(
                ResearchCheckpointIntegrity.session_id == session_id,
            ).first()
        except Exception as exc:
            raise ReviewWorkspaceUnavailable("无法读取检查点完整性记录") from exc
        if integrity is None:
            # Review access is a new, high-trust path.  Unlike generic restore,
            # it has no safe legacy compatibility mode.
            raise ReviewWorkspaceIntegrityError("待复核检查点缺少完整性证明")
        try:
            self._integrity_verifier._verify_integrity_pair(checkpoint, integrity, session_id)
        except CheckpointIntegrityError as exc:
            raise ReviewWorkspaceIntegrityError(f"待复核检查点完整性校验失败：{exc}") from exc
        except Exception as exc:
            raise ReviewWorkspaceUnavailable("检查点完整性校验服务失败") from exc

        state = checkpoint.state_json
        if not isinstance(state, dict):
            raise ReviewWorkspaceIntegrityError("待复核检查点状态不是对象")
        if state.get("session_id") != session_id:
            raise ReviewWorkspaceIntegrityError("待复核状态 session_id 不一致")
        assessment = state.get("risk_assessment")
        if not isinstance(assessment, dict):
            raise ReviewWorkspaceIntegrityError("待复核状态缺少风险评级")
        # The row's status is mutable metadata.  The sealed business state
        # proves whether the workflow still needs a human decision.
        review = assessment.get("human_review")
        already_completed = isinstance(review, dict) and review.get("completed") is True
        if require_pending and (
            assessment.get("requires_human_review") is not True or already_completed
        ):
            raise ReviewTaskNotPending("会话没有有效的待人工复核状态")
        if checkpoint.status != "paused":
            raise ReviewWorkspaceIntegrityError(
                "封签状态仍待人工复核，但检查点行不在暂停状态"
            )
        return self._build_packet(checkpoint, state, assessment)

    @staticmethod
    def _summary(packet: ReviewTaskPacket) -> ReviewTaskSummary:
        return ReviewTaskSummary(
            session_id=packet.session_id,
            company_name=packet.company_name,
            created_at=packet.created_at,
            updated_at=packet.updated_at,
            profile_ref=packet.profile_ref,
            risk_assessment=packet.risk_assessment,
            completeness=packet.completeness,
        )

    def _build_packet(
        self, checkpoint: ResearchCheckpoint, state: Dict[str, Any], assessment: Dict[str, Any],
    ) -> ReviewTaskPacket:
        company_name = state.get("company_name") or state.get("subject_name") or "待核实主体"
        if not isinstance(company_name, str):
            raise ReviewWorkspaceIntegrityError("待复核状态企业名称非法")
        final_report = state.get("final_report", "")
        if not isinstance(final_report, str):
            raise ReviewWorkspaceIntegrityError("待复核状态报告正文非法")
        return ReviewTaskPacket(
            session_id=checkpoint.session_id,
            company_name=company_name,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.updated_at,
            profile_ref=self._profile_ref(state.get("admin_profile_ref")),
            final_report=final_report,
            risk_assessment=self._risk(assessment),
            completeness=self._completeness(state.get("completeness")),
            critical_issues=self._critical_issues(state.get("critic_feedback")),
            errors=_string_list(state.get("errors"), "errors"),
            evidence=self._evidence(state.get("evidence_store")),
        )

    @staticmethod
    def _profile_ref(value: Any) -> Optional[ReviewProfileRef]:
        if value is None or value == {}:
            return None
        if not isinstance(value, dict):
            raise ReviewWorkspaceIntegrityError("档案引用非法")
        profile_id = value.get("id")
        revision = value.get("revision")
        digest = value.get("content_sha256")
        if (
            not isinstance(profile_id, str) or not profile_id
            or not _is_int(revision) or revision < 1
            or not isinstance(digest, str) or len(digest) != 64
            or value.get("source") != "admin_company_profile"
        ):
            raise ReviewWorkspaceIntegrityError("档案引用形状非法")
        return ReviewProfileRef(
            id=profile_id, revision=revision, content_sha256=digest,
            source="admin_company_profile",
        )

    @staticmethod
    def _risk(value: Dict[str, Any]) -> ReviewRiskAssessment:
        recommendation = value.get("credit_recommendation")
        if recommendation is not None and not isinstance(recommendation, dict):
            raise ReviewWorkspaceIntegrityError("额度建议非法")
        recommendation_packet = None
        if recommendation is not None:
            recommendable = recommendation.get("recommendable")
            if recommendable is not None and not isinstance(recommendable, bool):
                raise ReviewWorkspaceIntegrityError("额度建议 recommendable 非法")
            recommendation_packet = ReviewCreditRecommendation(
                recommendable=recommendable,
                suggested_amount=_optional_number(
                    recommendation.get("suggested_amount"),
                    "credit_recommendation.suggested_amount",
                ),
                currency=_optional_string(recommendation.get("currency"), "credit_recommendation.currency"),
                based_on_level=_optional_string(
                    recommendation.get("based_on_level"), "credit_recommendation.based_on_level",
                ),
                advice_text=_optional_string(recommendation.get("advice_text"), "credit_recommendation.advice_text"),
                conditions=_string_list(recommendation.get("conditions"), "credit_recommendation.conditions"),
            )
        return ReviewRiskAssessment(
            level=_optional_string(value.get("level"), "risk_assessment.level"),
            composite_score=_optional_number(value.get("composite_score"), "risk_assessment.composite_score"),
            credit_advice=_optional_string(value.get("credit_advice"), "risk_assessment.credit_advice"),
            credit_recommendation=recommendation_packet,
            gates_applied=_string_list(value.get("gates_applied"), "risk_assessment.gates_applied"),
            triggered_rules=_triggered_rule_summaries(value.get("triggered_rules")),
        )

    @staticmethod
    def _completeness(value: Any) -> ReviewCompleteness:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ReviewWorkspaceIntegrityError("核查完整度非法")
        return ReviewCompleteness(
            required_total=_optional_int(value.get("required_total"), "completeness.required_total"),
            required_verified=_optional_int(value.get("required_verified"), "completeness.required_verified"),
            verified_rate=_optional_number(value.get("verified_rate"), "completeness.verified_rate"),
            unverified_fields=_string_list(value.get("unverified_fields"), "completeness.unverified_fields"),
            conflicting_fields=_string_list(value.get("conflicting_fields"), "completeness.conflicting_fields"),
        )

    @staticmethod
    def _critical_issues(value: Any) -> List[ReviewCriticalIssue]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ReviewWorkspaceIntegrityError("评审问题列表非法")
        result = []
        for item in value:
            if not isinstance(item, dict) or item.get("severity") != "critical":
                continue
            # A critic's free-form internal reasoning is deliberately omitted.
            result.append(ReviewCriticalIssue(
                issue_type=_optional_string(item.get("issue_type") or item.get("type"), "critical_issue.issue_type"),
                description=_optional_string(item.get("description") or item.get("issue"), "critical_issue.description"),
                severity="critical",
            ))
        return result

    @staticmethod
    def _evidence(value: Any) -> List[ReviewEvidenceSummary]:
        if value is None:
            return []
        if not isinstance(value, dict):
            raise ReviewWorkspaceIntegrityError("证据库非法")
        result = []
        for evidence_id, item in value.items():
            if not isinstance(evidence_id, str) or not isinstance(item, dict):
                raise ReviewWorkspaceIntegrityError("证据摘要非法")
            result.append(ReviewEvidenceSummary(
                evidence_id=evidence_id,
                field_id=_optional_string(item.get("field_id"), "evidence.field_id"),
                source_adapter=_optional_string(item.get("source_adapter"), "evidence.source_adapter"),
                retrieved_at=_optional_string(item.get("retrieved_at"), "evidence.retrieved_at"),
                as_of_date=_optional_string(item.get("as_of_date"), "evidence.as_of_date"),
                active=item.get("active") if isinstance(item.get("active"), bool) else None,
            ))
        return result
