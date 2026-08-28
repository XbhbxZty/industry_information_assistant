# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
单次调用的墙钟上界 + 章节级失败隔离与留痕（BC-56）

## 这一轮在钉什么

BC-56 的三条根因里，前两条（模型路由被拍平、提示词索取过多）能靠配置和
提示词解决，第三条不能：`call_llm` **从来没有超时概念**，实测有单个 Scout
调用耗时 553 秒，期间整张图没有任何产出，也没有任何一层能中止它。
裁剪提示词只是把均值压下去，尾部依然无界。

同时 `asyncio.gather(*tasks)` 没有 `return_exceptions`，任意一章的异常会让
另外七章的抽取结果**全部丢弃**——这正是 BC-55 那个 `list.get` 把整轮打成
facts=0 的机制。

## 断言分三层

1. **上界真的存在且下到了 SDK**：超时必须作为 per-request 参数传给 httpx。
   用 `asyncio.wait_for` 包 `asyncio.to_thread` 是**错的**：to_thread 不可
   取消，超时后线程仍在阻塞等响应，每次泄漏一个线程。
2. **隔离与留痕成对**：一章崩了不影响其余章节；同时必须留痕。
   只加隔离会让事情更糟——崩掉的章节产出 0 条证据，与"这一章确实没有材料"
   在下游完全无法区分（BC-51 在第四个入口）。
3. **失败可见到产出层**：附录单独成表并带免责声明，评测器判 invalid
   而不是 fail——"这一轮不算数"与"这一轮不达标"是两件事。

运行：cd backend && python -m pytest tests/test_llm_budget_and_isolation.py -q
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import openai  # noqa: E402

from service.deep_research_v2.agents.base import (  # noqa: E402
    DEFAULT_LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES, LLMCallTimeout,
)
from service.deep_research_v2.agents.scout import DeepScout  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402
from service.deep_research_v2.state import create_initial_state  # noqa: E402
from service.evidence_appendix import render_appendix  # noqa: E402
from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402


def _scout() -> DeepScout:
    return DeepScout(
        llm_api_key="test-key",
        llm_base_url="http://localhost:1/v1",
        search_api_key="test-search-key",
    )


def _state():
    state = create_initial_state("尽调", "test-session", due_diligence=True)
    # Scout 只在 PLANNING/RESEARCHING 阶段工作；init 阶段会直接返回。
    state["phase"] = ResearchPhase.RESEARCHING.value
    state["search_web"], state["search_local"] = False, True
    state["outline"] = [
        {"id": "sec_1", "title": "企业基本情况", "status": "pending"},
        {"id": "sec_4", "title": "财务分析", "status": "pending"},
    ]
    return state


# ------------------------------------------------- 一、上界存在且下到 SDK

def test_timeout_is_passed_to_the_sdk_request_not_wrapped_in_wait_for():
    """超时必须是 SDK 的 per-request 参数。

    这一条是整组里最重要的：`asyncio.to_thread` 交给线程池执行且**不可取消**，
    用 `wait_for` 包住只会让协程提前返回、线程继续阻塞——每次超时泄漏一个
    线程，默认线程池很快占满，后续调用连排队都排不上。只有 httpx 自己断连
    线程才会真正结束。
    """
    scout = _scout()
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop-after-capturing-kwargs")

    scout.client.chat.completions.create = fake_create
    try:
        asyncio.run(scout.call_llm("sys", "user"))
    except RuntimeError:
        pass

    assert "timeout" in seen, "call_llm 必须把 timeout 传给 SDK 请求"
    assert seen["timeout"] == DEFAULT_LLM_TIMEOUT_SECONDS


def test_per_node_budget_overrides_the_default():
    scout = _scout()
    scout.llm_timeout = 30.0
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop")

    scout.client.chat.completions.create = fake_create
    try:
        asyncio.run(scout.call_llm("sys", "user"))
    except RuntimeError:
        pass
    assert seen["timeout"] == 30.0, "高频抽取节点必须能单独收紧上界"

    seen.clear()
    try:
        asyncio.run(scout.call_llm("sys", "user", timeout=120.0))
    except RuntimeError:
        pass
    assert seen["timeout"] == 120.0, "单次调用也要能显式放宽"


def test_worst_case_wall_clock_is_bounded_by_explicit_retry_count():
    """SDK 默认重试 2 次会把上界放大到 3×timeout；必须显式收紧成可计算的值。"""
    scout = _scout()
    assert LLM_MAX_RETRIES == 1
    assert scout.client.max_retries == LLM_MAX_RETRIES, \
        "重试次数必须显式设定，否则墙钟上界是 SDK 默认值的隐式函数"


def test_timeout_raises_typed_error_not_generic_exception():
    """超时要有专门类型，调用方才能把它记成故障而不是『这次没抽到证据』。"""
    scout = _scout()

    def fake_create(**kwargs):
        raise openai.APITimeoutError(request=None)

    scout.client.chat.completions.create = fake_create
    try:
        asyncio.run(scout.call_llm("sys", "user"))
    except LLMCallTimeout as exc:
        assert exc.model == scout.model
        assert exc.timeout == DEFAULT_LLM_TIMEOUT_SECONDS
        return
    raise AssertionError("超时必须抛 LLMCallTimeout，不能只记日志或抛裸 Exception")


def test_timeout_is_not_swallowed_into_an_empty_result():
    """反面：超时绝不能被 call_llm 变成空字符串/空对象返回。"""
    scout = _scout()

    def fake_create(**kwargs):
        raise openai.APITimeoutError(request=None)

    scout.client.chat.completions.create = fake_create
    raised = False
    try:
        asyncio.run(scout.call_llm("sys", "user"))
    except LLMCallTimeout:
        raised = True
    assert raised, "把超时吞成空结果，就是把故障伪装成『查了没有』"


# --------------------------------------------- 二、隔离与留痕必须成对出现

def _run_process_with_sections(scout, state, behaviors):
    """让 _research_section 按 section_id 执行给定行为，其余流程照常。"""
    async def fake_section(st, section):
        behavior = behaviors.get(section["id"])
        if isinstance(behavior, BaseException):
            raise behavior
        st.setdefault("facts", []).append(
            {"id": f"fact_{section['id']}", "content": f"{section['id']} 的事实"}
        )

    scout._research_section = fake_section
    return asyncio.run(scout.process(state))


def test_one_crashed_section_does_not_discard_the_other_sections():
    scout, state = _scout(), _state()
    _run_process_with_sections(scout, state, {
        "sec_1": AttributeError("'list' object has no attribute 'get'"),
    })
    contents = [f["content"] for f in state["facts"]]
    assert "sec_4 的事实" in contents, \
        "裸 gather 会因 sec_1 的异常丢掉 sec_4 的全部结果——这正是 BC-55 打成 facts=0 的机制"


def test_crashed_section_is_recorded_as_a_failure_not_as_empty_evidence():
    scout, state = _scout(), _state()
    _run_process_with_sections(scout, state, {
        "sec_1": AttributeError("'list' object has no attribute 'get'"),
    })
    failures = state["section_failures"]
    assert len(failures) == 1
    assert failures[0]["section_id"] == "sec_1"
    assert failures[0]["failure_kind"] == "exception"
    assert "AttributeError" in failures[0]["failure_reason"]
    assert any("企业基本情况" in e for e in state["errors"]), \
        "阻断级失败必须进 errors，否则编排层的完成判据读不到它"


def test_llm_timeout_in_a_section_is_recorded_with_its_own_kind():
    scout, state = _scout(), _state()
    _run_process_with_sections(scout, state, {
        "sec_4": LLMCallTimeout("DeepScout", "qwen3.8-max", 90.0, 90211),
    })
    assert state["section_failures"][0]["failure_kind"] == "llm_timeout", \
        "挂死与逻辑崩溃要能分开统计，否则无法判断该调超时还是该修代码"


def test_successful_but_empty_section_is_not_recorded_as_a_failure():
    """互为反面的那一条：这一条在修复前后都必须通过。

    章节正常跑完但没抽到证据，是可以写进报告的结论；把它记成故障会让
    附录里出现一堆假故障，读者反而学会忽略这张表。
    """
    scout, state = _scout(), _state()

    async def empty_section(st, section):
        return None

    scout._research_section = empty_section
    asyncio.run(scout.process(state))
    assert state["section_failures"] == [], \
        "『查了没有』不是故障——与 SearchOutcome 同一条纪律"


def test_section_failure_pushes_an_sse_event_with_the_disclaimer():
    scout, state = _scout(), _state()
    pushed = []
    scout.add_message = lambda st, kind, payload: pushed.append((kind, payload))
    _run_process_with_sections(scout, state, {"sec_1": RuntimeError("boom")})
    events = [p for k, p in pushed if k == "section_failed"]
    assert events, "失败必须向上暴露；只写日志等于没人知道"
    assert "不得据此认定材料未提供" in events[0]["note"]


def test_user_cancellation_is_not_recorded_as_a_system_failure():
    scout, state = _scout(), _state()
    try:
        _run_process_with_sections(scout, state, {
            "sec_1": asyncio.CancelledError(),
        })
    except asyncio.CancelledError:
        assert state.get("section_failures") == [], \
            "用户取消不是系统故障，不得留痕、不得降级"
        return
    raise AssertionError("取消必须原样上抛给编排层，不能被隔离逻辑吃掉")


# ------------------------------------------------- 三、失败可见到产出层

def test_appendix_lists_section_failures_separately_from_search_failures():
    checks = build_field_checks(checked_at="2026-08-17")
    block = render_appendix(
        checks, {}, compute_completeness(checks),
        search_failures=[{"provider": "local_kb", "query": "涉诉",
                          "failure_reason": "Milvus 不可用",
                          "occurred_at": "2026-08-17T00:00:00"}],
        section_failures=[{"section_id": "sec_4", "section_title": "财务分析",
                           "failure_kind": "llm_timeout",
                           "failure_reason": "LLMCallTimeout: 超过 90 秒上界",
                           "occurred_at": "2026-08-17T00:00:00"}],
    )
    assert "本次未能完成的检索" in block
    assert "本次未能完成的章节抽取" in block, "章节故障必须单独成表"
    assert "财务分析" in block and "调用超时" in block
    assert "不得读作「材料未提供」" in block, "缺免责声明，读者会把故障读成结论"


def test_appendix_omits_the_section_failure_table_when_there_is_none():
    checks = build_field_checks(checked_at="2026-08-17")
    block = render_appendix(checks, {}, compute_completeness(checks))
    assert "本次未能完成的章节抽取" not in block


# 评测器侧的判定（invalid ≠ fail）在 test_real_case_processing.py 里断言，
# 那里有真实案例目录可用，能经由 score_run() 走生产路径而不是在测试里
# 自己拼一个 verdict——BC-49 的教训：纯函数/自拼装置无法回答"有没有接上"。


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
