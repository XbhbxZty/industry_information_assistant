"""Critic 评测装置自身的行为断言。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from eval import run_critic  # noqa: E402


class _BrokenCritic:
    async def _review_content(self, state):
        raise ConnectionError("模拟模型 API 断线")

    def merge_review(self, state, llm_result):
        # 模拟生产降级：完整组可能仍由扫描器找到问题。
        return {
            "degraded": True,
            "overall_assessment": {"verdict": "needs_revision", "quality_score": 3},
            "issues": [{
                "issue_type": "unverified_as_fact", "severity": "critical",
                "detected_by": "llm", "description": "模型报出的 critical 级问题",
            }],
        }


def test_LLM断线不得被计为扫描器消融贡献():
    companies = run_critic._load(run_critic.EVAL_DATA)["companies"]
    company = next(c for c in companies if c["company_id"] == "EVAL-003")
    case = run_critic._load(run_critic.CASES)["injection_cases"][0]
    original = run_critic._make_critic
    run_critic._RAW_LOG.clear()
    run_critic._make_critic = lambda: _BrokenCritic()
    try:
        result = asyncio.run(
            run_critic._run_once(asyncio.Semaphore(1), company, case, "injection")
        )
    finally:
        run_critic._make_critic = original

    assert result["ok"] is None, "模型不可用的调用必须退出统计分母"
    assert "ConnectionError" in result["error"]
    assert run_critic._RAW_LOG[-1]["degraded"] is True
    assert run_critic._RAW_LOG[-1]["ok"] is None


def test_全是调用错误时用例状态必须为ERROR():
    async def _failed_once(*args, **kwargs):
        return {"ok": None, "error": "API unavailable", "issues": []}

    original = run_critic._run_once
    run_critic._run_once = _failed_once
    try:
        result = asyncio.run(
            run_critic.run_case(asyncio.Semaphore(1), {}, {"id": "X"}, "clean", 3)
        )
    finally:
        run_critic._run_once = original

    assert result["verdict"] == "ERROR"
    assert result["n"] == 0
    assert len(result["errors"]) == 3


def test_holdout结构完整且ID不与开发集重复():
    dev = run_critic._load(run_critic.CASES)
    holdout = run_critic._load(
        os.path.join(os.path.dirname(run_critic.CASES), "critic_holdout.json")
    )
    dev_ids = {c["id"] for k in ("injection_cases", "clean_cases") for c in dev[k]}
    holdout_cases = holdout["injection_cases"] + holdout["clean_cases"]
    holdout_ids = [c["id"] for c in holdout_cases]
    company_ids = {
        c["company_id"] for c in run_critic._load(run_critic.EVAL_DATA)["companies"]
    }

    assert len(holdout["injection_cases"]) == 6
    assert len(holdout["clean_cases"]) == 8
    assert len(holdout_ids) == len(set(holdout_ids))
    assert not dev_ids.intersection(holdout_ids)
    assert all(c["based_on"] in company_ids for c in holdout_cases)
    assert all(c.get("section") and c.get("text") for c in holdout_cases)
    assert all(c.get("violation") for c in holdout["injection_cases"])


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
