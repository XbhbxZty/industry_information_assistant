"""Strict queue and minimum-packet tests for phase 3.4C2b."""
import os
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.reviewer_policy import ReviewerPolicy  # noqa: E402
import router.auth_router as auth_router_module  # noqa: E402
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity  # noqa: E402
from router.auth_router import user_response_for  # noqa: E402
from service.checkpoint_integrity import (  # noqa: E402
    INTEGRITY_VERSION,
    MODE_STANDARD,
    business_key_id,
    issue_business_state_seal,
    issue_graph_state_seal,
)
from service.review_workspace_service import (  # noqa: E402
    ReviewTaskNotPending,
    ReviewWorkspaceIntegrityError,
    ReviewWorkspaceService,
)


_OWNER = uuid.UUID("00000000-0000-0000-0000-000000000001")
_REVIEWER = uuid.UUID("00000000-0000-0000-0000-000000000002")


class _Query:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        # The production code uses SQLAlchemy predicates.  This deliberately
        # tiny fake only needs to honor a session-id equality predicate so an
        # integrity row cannot be accidentally reused for a different task.
        for arg in args:
            value = getattr(getattr(arg, "right", None), "value", None)
            if isinstance(value, str) and value != "paused":
                self.values = [row for row in self.values if getattr(row, "session_id", None) == value]
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _DB:
    def __init__(self, checkpoints, integrities):
        self.checkpoints = checkpoints
        self.integrities = integrities

    def query(self, model):
        if model is ResearchCheckpoint:
            return _Query(self.checkpoints)
        if model is ResearchCheckpointIntegrity:
            return _Query(self.integrities)
        raise AssertionError(model)


def _state(session_id="review-1", *, pending=True):
    state = {
        "session_id": session_id,
        "company_name": "测试企业",
        "final_report": "# 已封签的报告正文",
        "risk_assessment": {
            "level": "中风险",
            "composite_score": 61.5,
            "credit_advice": "追加担保后可审议",
            "credit_recommendation": {
                "recommendable": True,
                "suggested_amount": 1000000,
                "currency": "CNY",
                "based_on_level": "中风险",
                "conditions": ["追加担保"],
                "internal_formula": "must-not-leak",
            },
            "gates_applied": ["核查不完整"],
            "triggered_rules": [{
                "dimension": "judicial", "score": 80, "detail": "存在司法记录",
                "field_id": "litigation", "evidence": ["must-not-leak"],
            }],
            "requires_human_review": pending,
        },
        "completeness": {
            "required_total": 20,
            "required_verified": 10,
            "verified_rate": 0.5,
            "unverified_fields": ["司法诉讼"],
            "conflicting_fields": [],
            "private_future_key": "must-not-leak",
        },
        "critic_feedback": [{
            "severity": "critical", "type": "evidence_gap",
            "issue": "缺少司法来源", "internal_reasoning": "must-not-leak",
        }],
        "errors": ["司法接口超时"],
        "evidence_store": {
            "ev-1": {
                "field_id": "litigation", "source_adapter": "court",
                "retrieved_at": "2026-01-01T00:00:00", "as_of_date": "2025-12-31",
                "active": True, "raw": {"token": "must-not-leak"},
                "profile_patch": {"must": "not-leak"}, "value": "must-not-leak",
            }
        },
        "messages": [{"prompt": "must-not-leak"}],
        "kb_scope": [{"collection": "must-not-leak"}],
        "provided_company_profile": {"secret": "must-not-leak"},
    }
    state["checkpoint_graph_seal"] = issue_graph_state_seal(state, MODE_STANDARD)
    return state


def _checkpoint(state, *, status="paused", owner=_OWNER, integrity=True):
    checkpoint = ResearchCheckpoint(
        id=uuid.uuid4(), session_id=state["session_id"], user_id=owner,
        query="尽调", phase="reviewing", iteration=1, state_json=state,
        ui_state_json={"secret": "must-not-leak"}, final_report="unsealed-row-report",
        status=status, created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 2),
    )
    if not integrity:
        return checkpoint, None
    row = ResearchCheckpointIntegrity(
        session_id=state["session_id"], checkpoint_id=str(checkpoint.id),
        mode=MODE_STANDARD, integrity_version=INTEGRITY_VERSION,
        key_id=business_key_id(MODE_STANDARD), business_revision=1,
        business_seal=issue_business_state_seal(state, state["session_id"], MODE_STANDARD, 1),
    )
    return checkpoint, row


@pytest.fixture(autouse=True)
def _integrity_key(monkeypatch):
    monkeypatch.setenv("ADMIN_PROFILE_SNAPSHOT_HMAC_KEY", "review-workspace-test-key")


def test_packet_is_a_handbuilt_minimum_projection_and_uses_sealed_report():
    state = _state()
    checkpoint, integrity = _checkpoint(state)
    packet = ReviewWorkspaceService(_DB([checkpoint], [integrity])).get_pending("review-1", _REVIEWER)
    payload = packet.model_dump()

    assert payload["final_report"] == "# 已封签的报告正文"
    assert payload["risk_assessment"]["credit_recommendation"]["suggested_amount"] == 1000000.0
    assert payload["risk_assessment"]["triggered_rules"] == ["存在司法记录"]
    rendered = repr(payload)
    for forbidden in ("state_json", "ui_state_json", "messages", "kb_scope", "raw", "profile_patch",
                      "must-not-leak", "unsealed-row-report", "internal_formula", "private_future_key"):
        assert forbidden not in rendered


def test_legacy_or_tampered_pending_task_fails_closed():
    state = _state()
    checkpoint, _ = _checkpoint(state, integrity=False)
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([checkpoint], [])).get_pending("review-1", _REVIEWER)

    checkpoint, integrity = _checkpoint(_state())
    checkpoint.state_json["final_report"] = "tampered"
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([checkpoint], [integrity])).get_pending("review-1", _REVIEWER)


def test_queue_filters_self_and_stale_nonpending_candidates_but_not_corruption():
    valid, valid_integrity = _checkpoint(_state("valid"))
    own, own_integrity = _checkpoint(_state("own"), owner=_REVIEWER)
    stale, stale_integrity = _checkpoint(_state("stale", pending=False))

    tasks = ReviewWorkspaceService(_DB(
        [valid, own, stale], [valid_integrity, own_integrity, stale_integrity],
    )).list_pending(_REVIEWER)
    assert [task.session_id for task in tasks] == ["valid"]

    damaged, damaged_integrity = _checkpoint(_state("damaged"))
    damaged.state_json["company_name"] = "tampered"
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([damaged], [damaged_integrity])).list_pending(_REVIEWER)


def test_detail_forbids_self_review_and_nonpaused_or_nonpending_targets():
    state = _state()
    checkpoint, integrity = _checkpoint(state)
    service = ReviewWorkspaceService(_DB([checkpoint], [integrity]))
    with pytest.raises(ReviewTaskNotPending):
        service.get_pending("review-1", _OWNER)

    checkpoint, integrity = _checkpoint(_state(), status="completed")
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([checkpoint], [integrity])).get_pending("review-1", _REVIEWER)

    # A mutable status mismatch must not hide a still-sealed pending task from
    # the queue as an apparently harmless empty result.
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([checkpoint], [integrity])).list_pending(_REVIEWER)


def test_submission_authorization_uses_the_same_strict_pending_reader():
    checkpoint, integrity = _checkpoint(_state())
    service = ReviewWorkspaceService(_DB([checkpoint], [integrity]))
    assert service.authorize_submission("review-1", _REVIEWER) == str(_OWNER)

    legacy, _ = _checkpoint(_state("legacy"), integrity=False)
    with pytest.raises(ReviewWorkspaceIntegrityError):
        ReviewWorkspaceService(_DB([legacy], [])).authorize_submission("legacy", _REVIEWER)


def test_user_response_capability_is_server_computed(monkeypatch):
    user = SimpleNamespace(
        id=_REVIEWER, username="reviewer", email="reviewer@example.com",
        is_active=True, is_superuser=False, created_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(auth_router_module, "REVIEWER_POLICY", ReviewerPolicy(frozenset({_REVIEWER})))
    assert user_response_for(user).can_human_review is True
    user.is_superuser = False
    monkeypatch.setattr(auth_router_module, "REVIEWER_POLICY", ReviewerPolicy(frozenset()))
    assert user_response_for(user).can_human_review is False


def test_oauth_token_login_rejects_inactive_reviewer_before_issuing_token(monkeypatch):
    user = SimpleNamespace(
        id=_REVIEWER, username="disabled-reviewer", email="reviewer@example.com",
        is_active=False, is_superuser=True, created_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(auth_router_module, "authenticate_user", lambda *_args: user)
    token_issued = False

    def _unexpected_token(**_kwargs):
        nonlocal token_issued
        token_issued = True
        return "should-not-be-issued"

    monkeypatch.setattr(auth_router_module, "create_access_token", _unexpected_token)
    form = SimpleNamespace(username="disabled-reviewer", password="secret")

    with pytest.raises(auth_router_module.HTTPException) as exc_info:
        import asyncio
        asyncio.run(auth_router_module.login_for_token(form, object()))

    assert exc_info.value.status_code == 403
    assert token_issued is False
