"""研究检查点与人工复核入口的身份边界。"""
import asyncio
import importlib
import logging
import os
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from config import reviewer_policy as reviewer_policy_module  # noqa: E402
auth_router_module = importlib.import_module("router.auth_router")  # noqa: E402
research_router_module = importlib.import_module("router.research_router")  # noqa: E402
from config.reviewer_policy import ReviewerPolicy, parse_human_reviewer_user_ids  # noqa: E402
from router.auth_router import get_current_user_required  # noqa: E402
from router.research_router import (  # noqa: E402
    HumanReviewRequest,
    _assert_checkpoint_access,
    _assert_review_target,
    get_checkpoint,
    router,
)


_OWNER_ID = "00000000-0000-0000-0000-000000000001"
_REVIEWER_ID = "00000000-0000-0000-0000-000000000002"
_OTHER_ID = "00000000-0000-0000-0000-000000000003"


def _user(user_id="user-1", *, is_superuser=False, username="reviewer"):
    return SimpleNamespace(
        id=user_id,
        is_superuser=is_superuser,
        is_active=True,
        username=username,
    )


def test_检查点归属用户可以访问():
    _assert_checkpoint_access({"user_id": "user-1"}, _user())


def test_其他用户不能访问检查点():
    try:
        _assert_checkpoint_access({"user_id": "user-2"}, _user())
        raise AssertionError("其他用户不应能访问该检查点")
    except HTTPException as e:
        assert e.status_code == 403


def test_路由不得把归属校验的403包装成500():
    import service.checkpoint_service as checkpoint_module

    class _CheckpointService:
        def get_checkpoint_info(self, session_id):
            return {"session_id": session_id, "user_id": "user-2"}

    original = checkpoint_module.get_checkpoint_service
    checkpoint_module.get_checkpoint_service = lambda: _CheckpointService()
    try:
        try:
            asyncio.run(get_checkpoint("session-1", _user()))
            raise AssertionError("其他用户访问不应成功")
        except HTTPException as e:
            assert e.status_code == 403, "HTTPException 必须原样透传，不能被包装成 500"
    finally:
        checkpoint_module.get_checkpoint_service = original


def test_无归属的历史检查点默认失败关闭():
    try:
        _assert_checkpoint_access({"user_id": None}, _user())
        raise AssertionError("无归属检查点不应退化为公共数据")
    except HTTPException as e:
        assert e.status_code == 403


def test_超级用户可执行跨用户复核与运维():
    _assert_checkpoint_access({"user_id": "user-2"}, _user(is_superuser=True))
    _assert_checkpoint_access({"user_id": None}, _user(is_superuser=True))


def test_复核请求不接受客户端伪造复核人():
    try:
        HumanReviewRequest(approved=True, reviewer="冒名用户")
        raise AssertionError("复核人必须由 Token 身份生成，不能接受请求体覆盖")
    except ValidationError as e:
        assert "reviewer" in str(e)


def test_复核请求只提交业务决定():
    request = HumanReviewRequest(approved=True, comment="同意")
    assert request.model_dump() == {
        "approved": True,
        "comment": "同意",
        "override_level": None,
    }


# ================================================== 专用 reviewer 权限

def _policy(*ids: str) -> ReviewerPolicy:
    return ReviewerPolicy(frozenset(UUID(user_id) for user_id in ids))


class _CheckpointService:
    def __init__(self, info):
        self.info = info
        self.info_calls = 0
        self.delete_calls = 0
        self.list_kwargs = None

    def get_checkpoint_info(self, _session_id):
        self.info_calls += 1
        return self.info

    def list_checkpoints(self, **kwargs):
        self.list_kwargs = kwargs
        return []

    def delete_checkpoint(self, _session_id):
        self.delete_calls += 1
        return True


class _ReviewService:
    def __init__(self):
        self.calls = []

    async def submit_review(self, session_id, decision, user_id):
        self.calls.append((session_id, decision, user_id))
        yield "data: [DONE]\n\n"


class _FailingReviewService:
    async def submit_review(self, _session_id, _decision, user_id):
        del user_id
        if False:  # keep this an async generator so failure occurs in the SSE stream
            yield ""
        raise RuntimeError("review stream boom")


def _review_client(
    monkeypatch, user, info, policy, review_service=None, workspace_error=None,
):
    """Exercise the actual nested FastAPI dependency chain for review routes."""
    import service.checkpoint_service as checkpoint_module

    class _StrictWorkspaceService:
        def __init__(self, _db):
            pass

        def authorize_submission(self, _session_id, reviewer_id):
            if workspace_error is not None:
                raise workspace_error
            if info is None:
                raise research_router_module.ReviewTaskNotFound("会话不存在")
            owner_id = info.get("user_id")
            if not owner_id:
                raise research_router_module.ReviewTaskNotPending("无归属会话")
            if str(owner_id) == str(reviewer_id):
                raise research_router_module.ReviewTaskNotPending("不得自审")
            if info.get("status") != "paused":
                raise research_router_module.ReviewTaskNotPending("当前不在暂停复核状态")
            return str(owner_id)

        def list_pending(self, _reviewer_id):
            if workspace_error is not None:
                raise workspace_error
            return []

    checkpoint_service = _CheckpointService(info)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_required] = lambda: user
    app.dependency_overrides[research_router_module.get_db] = lambda: object()
    monkeypatch.setattr(auth_router_module, "REVIEWER_POLICY", policy)
    monkeypatch.setattr(
        research_router_module,
        "ReviewWorkspaceService",
        _StrictWorkspaceService,
    )
    monkeypatch.setattr(
        checkpoint_module,
        "get_checkpoint_service",
        lambda: checkpoint_service,
    )
    if review_service is not None:
        monkeypatch.setattr(
            research_router_module,
            "get_research_service_v2",
            lambda: review_service,
        )
    return TestClient(app), checkpoint_service


def _review_payload():
    return {"approved": True, "comment": "已复核", "override_level": None}


def test_reviewer_allowlist_parser_is_strict_and_frozen(monkeypatch):
    assert parse_human_reviewer_user_ids(None) == frozenset()
    assert parse_human_reviewer_user_ids("   ") == frozenset()
    assert parse_human_reviewer_user_ids(f" {_REVIEWER_ID} ") == {UUID(_REVIEWER_ID)}

    for invalid in (
        ",",
        f"{_REVIEWER_ID},",
        f"{_REVIEWER_ID},, {_OTHER_ID}",
        "not-a-uuid",
        UUID(_REVIEWER_ID).hex,
        "{" + _REVIEWER_ID + "}",
    ):
        with pytest.raises((TypeError, ValueError)):
            parse_human_reviewer_user_ids(invalid)

    monkeypatch.setenv("DD_HUMAN_REVIEWER_USER_IDS", _REVIEWER_ID)
    frozen = ReviewerPolicy.from_environment()
    monkeypatch.delenv("DD_HUMAN_REVIEWER_USER_IDS")
    assert frozen.can_review(_REVIEWER_ID, is_superuser=False)

    module_policy = auth_router_module.REVIEWER_POLICY
    module_ids = module_policy.additional_reviewer_ids
    monkeypatch.setenv("DD_HUMAN_REVIEWER_USER_IDS", _OTHER_ID)
    assert auth_router_module.REVIEWER_POLICY is module_policy
    assert auth_router_module.REVIEWER_POLICY.additional_reviewer_ids == module_ids


def test_invalid_reviewer_config_disables_the_whole_extra_allowlist_without_leaking_value(
    monkeypatch, caplog,
):
    secret_like_bad_value = f"{_REVIEWER_ID},should-not-appear-in-logs"
    monkeypatch.setenv("DD_HUMAN_REVIEWER_USER_IDS", secret_like_bad_value)
    caplog.set_level(logging.ERROR, logger=reviewer_policy_module.__name__)

    policy = ReviewerPolicy.from_environment()

    assert policy.additional_reviewer_ids == frozenset()
    assert not policy.can_review(_REVIEWER_ID, is_superuser=False)
    assert secret_like_bad_value not in caplog.text
    assert "DD_HUMAN_REVIEWER_USER_IDS" in caplog.text


def test_review_target_helper_requires_owner_and_separation_for_all_roles():
    reviewer = _user(_REVIEWER_ID)
    assert _assert_review_target({"user_id": _OWNER_ID}, reviewer) == _OWNER_ID

    for actor in (
        _user(_OWNER_ID),
        _user(_OWNER_ID, is_superuser=True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _assert_review_target({"user_id": _OWNER_ID}, actor)
        assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        _assert_review_target({"user_id": None}, reviewer)
    assert exc_info.value.status_code == 403


def test_review_without_token_returns_401_before_checkpoint_access():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).post("/research/review/missing", json=_review_payload())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    bad_token = TestClient(app).post(
        "/research/review/missing",
        json=_review_payload(),
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert bad_token.status_code == 401
    assert bad_token.headers["www-authenticate"] == "Bearer"


def test_non_reviewer_is_rejected_before_checkpoint_lookup(monkeypatch):
    client, checkpoint_service = _review_client(
        monkeypatch,
        _user(_OWNER_ID),
        {"session_id": "s", "user_id": _OWNER_ID, "status": "paused"},
        _policy(),
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == 403
    assert checkpoint_service.info_calls == 0


def test_review_workspace_reads_require_reviewer_capability(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    anonymous = TestClient(app)
    assert anonymous.get("/research/reviews").status_code == 401
    assert anonymous.get("/research/reviews/s").status_code == 401

    client, checkpoint_service = _review_client(
        monkeypatch,
        _user(_OWNER_ID),
        {"session_id": "s", "user_id": _OWNER_ID, "status": "paused"},
        _policy(),
    )
    assert client.get("/research/reviews").status_code == 403
    assert client.get("/research/reviews/s").status_code == 403
    assert checkpoint_service.info_calls == 0


def test_authorized_empty_review_queue_is_explicit(monkeypatch):
    client, _checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        None,
        _policy(_REVIEWER_ID),
    )

    response = client.get("/research/reviews")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_review_queue_failure_is_not_disguised_as_empty(monkeypatch):
    client, _checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        None,
        _policy(_REVIEWER_ID),
        workspace_error=research_router_module.ReviewWorkspaceUnavailable("数据库不可用"),
    )

    response = client.get("/research/reviews")

    assert response.status_code == 500
    assert response.json()["detail"] == "复核工作台暂不可用"


@pytest.mark.parametrize(
    ("actor", "policy"),
    [
        (_user(_REVIEWER_ID), _policy(_REVIEWER_ID)),
        (_user(_REVIEWER_ID, is_superuser=True), _policy()),
    ],
)
def test_authorized_nonowner_reviewer_submits_server_identity_and_owner_id(
    monkeypatch, actor, policy,
):
    review_service = _ReviewService()
    client, checkpoint_service = _review_client(
        monkeypatch,
        actor,
        {"session_id": "s", "user_id": _OWNER_ID, "status": "paused"},
        policy,
        review_service,
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == 200
    assert response.text == "data: [DONE]\n\n"
    assert checkpoint_service.info_calls == 0
    assert len(review_service.calls) == 1
    session_id, decision, graph_user_id = review_service.calls[0]
    assert session_id == "s"
    assert decision["reviewer"] == actor.username
    assert decision["reviewer_id"] == str(actor.id)
    assert graph_user_id == _OWNER_ID


def test_review_stream_failure_remains_a_framed_sse_error(monkeypatch):
    client, _checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        {"session_id": "s", "user_id": _OWNER_ID, "status": "paused"},
        _policy(_REVIEWER_ID),
        _FailingReviewService(),
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == 200
    assert response.text.startswith("data: ")
    assert response.text.endswith("\n\n")
    assert '"type": "error"' in response.text
    assert "review stream boom" in response.text


@pytest.mark.parametrize(
    ("actor", "policy"),
    [
        (_user(_REVIEWER_ID), _policy(_REVIEWER_ID)),
        (_user(_OWNER_ID, is_superuser=True), _policy()),
    ],
)
def test_any_authorized_role_is_forbidden_from_self_review(monkeypatch, actor, policy):
    owner_id = str(actor.id)
    client, checkpoint_service = _review_client(
        monkeypatch,
        actor,
        {"session_id": "s", "user_id": owner_id, "status": "paused"},
        policy,
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == 403
    assert checkpoint_service.info_calls == 0


@pytest.mark.parametrize(
    ("info", "expected_status"),
    [
        (None, 404),
        ({"session_id": "s", "user_id": None, "status": "paused"}, 403),
        ({"session_id": "s", "user_id": _OWNER_ID, "status": "completed"}, 403),
    ],
)
def test_reviewer_review_target_boundaries(monkeypatch, info, expected_status):
    client, checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        info,
        _policy(_REVIEWER_ID),
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == expected_status
    assert checkpoint_service.info_calls == 0


def test_direct_review_post_cannot_bypass_strict_integrity_reader(monkeypatch):
    review_service = _ReviewService()
    client, checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        {"session_id": "s", "user_id": _OWNER_ID, "status": "paused"},
        _policy(_REVIEWER_ID),
        review_service,
        workspace_error=research_router_module.ReviewWorkspaceIntegrityError(
            "待复核检查点缺少完整性证明"
        ),
    )

    response = client.post("/research/review/s", json=_review_payload())

    assert response.status_code == 409
    assert checkpoint_service.info_calls == 0
    assert review_service.calls == []


def test_allowlisted_reviewer_has_no_cross_owner_checkpoint_operator_access(monkeypatch):
    client, checkpoint_service = _review_client(
        monkeypatch,
        _user(_REVIEWER_ID),
        {"session_id": "other", "user_id": _OWNER_ID, "status": "paused"},
        _policy(_REVIEWER_ID),
    )

    assert client.get("/research/checkpoint/other").status_code == 403
    assert client.get("/research/checkpoint/other/full").status_code == 403
    assert client.delete("/research/checkpoint/other").status_code == 403
    assert client.post("/research/resume/other").status_code == 403
    assert client.post("/research/cancel/other").status_code == 403
    assert checkpoint_service.delete_calls == 0

    assert client.get("/research/checkpoints").status_code == 200
    assert checkpoint_service.list_kwargs["user_id"] == _REVIEWER_ID


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
