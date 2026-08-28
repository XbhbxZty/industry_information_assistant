"""研究检查点与人工复核入口的身份边界。"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from router.research_router import (  # noqa: E402
    HumanReviewRequest,
    _assert_checkpoint_access,
    get_checkpoint,
)


def _user(user_id="user-1", *, is_superuser=False):
    return SimpleNamespace(id=user_id, is_superuser=is_superuser)


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
