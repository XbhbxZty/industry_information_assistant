# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
检索故障与空结果的区分（P0-2）

## 这一轮在钉什么

`datasource/base.py` 的 `AdapterResult` 用 `queried` / `records` 两个字段把
"没查"和"查了没有"在类型层分开，注释写明这是 v0.2 撞出来的核心设计。
但 Scout 的检索路径从来没享受到同一条纪律：`_execute_search` 有四条失败路径
（HTTP 非 200 / 业务码非 200 / 超时 / 其它异常）和一条真空结果路径，
**五种情况返回同一个空列表**。

后果不是少几条结果，而是检索故障会被下游读成"未发现负面信息"。
舆情、监管处罚这类 `absence_meaningful=True` 的字段，"搜了没有"是合法的
正面结论，"没搜成"绝不是——把两者混同等于把一次 API 超时翻译成
"该企业无负面舆情"。与 BC-19 / BC-31 同形：机制在别处建好了，
另一个入口重新打开同一个洞。

## 断言分三层

1. **`SearchOutcome` 自身的不变量**：成功不得带失败原因，失败必须给原因
2. **五种路径的映射**：每一种失败都落到 ok=False，零结果落到 ok=True
3. **业务可见行为**：清单回写只认"查成功"，不认"有结果"；失败留痕并去重

运行：cd backend && python tests/test_search_outcome.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import requests  # noqa: E402

from config.dd_checklist import build_field_checks  # noqa: E402
from service.deep_research_v2.agents import scout as scout_mod  # noqa: E402
from service.deep_research_v2.agents.scout import DeepScout, SearchOutcome  # noqa: E402
from service.deep_research_v2.agents.scout import expand_local_search_queries  # noqa: E402
from service.deep_research_v2.state import create_initial_state  # noqa: E402


def _scout() -> DeepScout:
    """构造一个不发网络请求的 Scout。OpenAI 客户端在构造期不联网。"""
    return DeepScout(
        llm_api_key="test-key",
        llm_base_url="http://localhost:1/v1",
        search_api_key="test-search-key",
    )


def test_local_query_expansion_splits_multi_topic_architect_output():
    queries = ["2024年营业收入；资产负债率；经营活动现金流；应收账款净额"]
    assert expand_local_search_queries(queries) == [
        "2024年营业收入", "资产负债率", "经营活动现金流", "应收账款净额"
    ]


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"code": 200, "data": {}}
        self.text = text

    def json(self):
        return self._payload


def _ok_payload(n: int):
    return {
        "code": 200,
        "data": {"webPages": {"value": [
            {"url": f"https://example.com/{i}", "name": f"标题{i}",
             "snippet": f"摘要{i}", "siteName": "示例站", "datePublished": "2026-01-01"}
            for i in range(n)
        ]}},
    }


def _patch_post(monkey_result):
    """把 requests.post 换成给定行为；返回还原函数与调用计数器。"""
    calls = {"n": 0}
    original = scout_mod.requests.post

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if isinstance(monkey_result, Exception):
            raise monkey_result
        return monkey_result

    scout_mod.requests.post = fake_post
    return original, calls


def _restore_post(original):
    scout_mod.requests.post = original


# ---------------------------------------------------------- 一、类型不变量

def test_outcome_rejects_success_carrying_failure_reason():
    try:
        SearchOutcome(ok=True, failure_reason="超时", results=[])
    except ValueError:
        return
    raise AssertionError("成功的检索携带 failure_reason 应当被拒绝")


def test_outcome_rejects_failure_without_reason():
    try:
        SearchOutcome(ok=False)
    except ValueError:
        return
    raise AssertionError(
        "失败但不给原因应当被拒绝——否则它与空结果在下游依然不可区分"
    )


def test_searched_and_empty_is_true_only_for_successful_empty():
    assert SearchOutcome(ok=True, results=[]).searched_and_empty, \
        "查成功且零结果，才是可以写进报告的正面结论"
    assert not SearchOutcome(ok=True, results=[{"url": "x"}]).searched_and_empty
    assert not SearchOutcome(ok=False, failure_reason="超时").searched_and_empty, \
        "失败绝不能被当作『查了没有』"


# ------------------------------------------------ 二、五条路径各自落到哪里

def test_http_error_is_failure_not_empty():
    s = _scout()
    original, _ = _patch_post(_Resp(status_code=500, text="boom"))
    try:
        out = asyncio.run(s._execute_search("测试企业 涉诉"))
    finally:
        _restore_post(original)
    assert not out.ok, "HTTP 500 是检索故障，不是『查了没有』"
    assert "500" in out.failure_reason, out.failure_reason


def test_business_code_error_is_failure_not_empty():
    s = _scout()
    original, _ = _patch_post(_Resp(payload={"code": 403, "msg": "配额不足"}))
    try:
        out = asyncio.run(s._execute_search("测试企业 处罚"))
    finally:
        _restore_post(original)
    assert not out.ok, "接口业务码非 200 是检索故障"
    assert "403" in out.failure_reason and "配额" in out.failure_reason


def test_timeout_is_failure_not_empty():
    s = _scout()
    original, _ = _patch_post(requests.exceptions.Timeout())
    try:
        out = asyncio.run(s._execute_search("测试企业 失信"))
    finally:
        _restore_post(original)
    assert not out.ok, "超时是检索故障"
    assert "超时" in out.failure_reason


def test_unexpected_exception_is_failure_not_empty():
    s = _scout()
    original, _ = _patch_post(RuntimeError("连接被重置"))
    try:
        out = asyncio.run(s._execute_search("测试企业 担保"))
    finally:
        _restore_post(original)
    assert not out.ok
    assert "RuntimeError" in out.failure_reason


def test_zero_results_is_success_not_failure():
    s = _scout()
    original, _ = _patch_post(_Resp(payload=_ok_payload(0)))
    try:
        out = asyncio.run(s._execute_search("某企业 负面舆情"))
    finally:
        _restore_post(original)
    assert out.ok, "接口正常返回零条，是『查了没有』，不是故障"
    assert out.searched_and_empty


# ------------------------------------------------------------ 三、缓存语义

def test_failure_is_not_cached():
    """
    失败被缓存，等于让一次瞬时超时在整轮研究里反复复现为同一个空结果，
    且重试永远打不到真实数据源。
    """
    s = _scout()
    original, calls = _patch_post(requests.exceptions.Timeout())
    try:
        first = asyncio.run(s._execute_search("同一个查询"))
        second = asyncio.run(s._execute_search("同一个查询"))
    finally:
        _restore_post(original)
    assert not first.ok and not second.ok
    assert calls["n"] == 2, f"失败不得写缓存，重试须真正重发请求（实际发了 {calls['n']} 次）"


def test_success_is_cached():
    s = _scout()
    original, calls = _patch_post(_Resp(payload=_ok_payload(2)))
    try:
        first = asyncio.run(s._execute_search("可缓存查询"))
        second = asyncio.run(s._execute_search("可缓存查询"))
    finally:
        _restore_post(original)
    assert first.ok and second.ok
    assert len(second.results) == 2
    assert calls["n"] == 1, "成功结果应命中缓存"


# -------------------------------------------------- 四、失败留痕与去重

def _state_with_checks():
    st = create_initial_state(query="尽调 测试企业", session_id="s1")
    st["field_checks"] = build_field_checks(checked_at="2026-08-16T00:00:00")
    return st


def test_failure_is_recorded_in_state_and_deduped():
    s = _scout()
    st = _state_with_checks()
    out = SearchOutcome(ok=False, failure_reason="请求超时（30秒）",
                        provider="bocha_web_search", query="重复查询")

    s._record_search_failure(st, out)
    s._record_search_failure(st, out)

    assert len(st["search_failures"]) == 1, \
        "同一 (provider, query) 的重试不该被记成多个独立信息缺口"
    rec = st["search_failures"][0]
    assert rec["provider"] == "bocha_web_search"
    assert rec["failure_reason"] == "请求超时（30秒）"
    assert rec["occurred_at"], "失败必须带时间戳，否则无法判断它属于哪一轮"

    kinds = [m.get("type") for m in st["messages"]]
    assert "search_failed" in kinds, "检索故障必须向上暴露，只写日志等于没人看见"


# ------------------------------------- 五、业务可见行为：清单回写的判据

def _run_section(scout, state, web_outcome=None, local_outcome=None):
    """
    跑一次 `_research_section`，把两条检索都换成给定结果。

    两个 outcome 都不带结果时，函数在 `if not all_results` 处提前返回，
    因此不会触发任何 LLM 调用。
    """
    async def fake_web(query, count=10, as_of=""):
        return web_outcome

    async def fake_local(query, top_k=10, kb_scope=None):
        return local_outcome

    scout._execute_search = fake_web
    scout._execute_local_search = fake_local
    section = {"id": "sec_5", "title": "司法与合规风险", "search_queries": ["测试企业 涉诉"]}
    asyncio.run(scout._research_section(state, section))


def _attempted(state, field_id):
    for c in state["field_checks"]:
        if c["field_id"] == field_id:
            return c.get("attempted_sources") or []
    raise AssertionError(f"清单里没有 {field_id}")


def test_failed_search_does_not_mark_attempted_source():
    """
    这是本文件最重要的一条。

    `attempted_sources` 的含义是"这个来源确实查过了"。把失败也记进去，
    等于让一串超时在清单上呈现为"已尝试 web_search"，而复核人无从知道
    那次尝试根本没连上。
    """
    s = _scout()
    st = _state_with_checks()
    st["search_web"], st["search_local"] = True, False

    _run_section(s, st, web_outcome=SearchOutcome(
        ok=False, failure_reason="HTTP 503",
        provider="bocha_web_search", query="测试企业 涉诉"))

    assert "web_search" not in _attempted(st, "litigation"), \
        "检索失败不得在清单上留下『已尝试该来源』的假象"
    assert len(st["search_failures"]) == 1, "失败本身必须留痕"


def test_successful_but_empty_search_does_mark_attempted_source():
    """
    与上一条互为反面：查成功但零结果，是真的查过了，必须记。

    此前的判据是 `if all_results`，这种情况会被整个漏掉——
    清单上看不出这个源已经查过，核实率的分母因此失真。
    """
    s = _scout()
    st = _state_with_checks()
    st["search_web"], st["search_local"] = True, False

    _run_section(s, st, web_outcome=SearchOutcome(
        ok=True, results=[], provider="bocha_web_search", query="测试企业 涉诉"))

    assert "web_search" in _attempted(st, "litigation"), \
        "查成功且零结果是真实的检索尝试，必须记入 attempted_sources"
    assert not st["search_failures"], "成功的检索不该产生失败记录"


def test_status_never_flipped_by_search():
    """
    回归护栏：无论检索成功、失败还是零结果，都不得翻转 status。
    字段级核实要走结构化适配器（verification.py 规则 2）。
    """
    s = _scout()
    st = _state_with_checks()
    st["search_web"], st["search_local"] = True, False

    _run_section(s, st, web_outcome=SearchOutcome(
        ok=True, results=[], provider="bocha_web_search", query="测试企业 涉诉"))

    for c in st["field_checks"]:
        assert c["status"] in ("unverified", "not_applicable"), \
            f"{c['field_id']} 被通用检索翻转成了 {c['status']}"


def test_both_sources_recorded_when_both_succeed():
    """
    原实现用 `tag = "web_search" if search_web else "local_kb"`，
    两路都开时本地库那次检索永远不会被记录。
    """
    s = _scout()
    st = _state_with_checks()
    st["search_web"], st["search_local"] = True, True

    _run_section(
        s, st,
        web_outcome=SearchOutcome(ok=True, results=[], provider="bocha_web_search", query="q"),
        local_outcome=SearchOutcome(ok=True, results=[], provider="local_kb", query="q"),
    )

    attempted = _attempted(st, "litigation")
    assert "web_search" in attempted and "local_kb" in attempted, \
        f"两路检索都成功时应各记一条，实际 {attempted}"


def test_local_failure_does_not_ride_on_web_success():
    """
    本地库查失败、网络检索成功时，清单不得出现 local_kb。

    这正是本地检索死链（collection 名不匹配）当前的形态：
    它永远查不到，却不能因此让清单显示"本地知识库已查过"。
    """
    s = _scout()
    st = _state_with_checks()
    st["search_web"], st["search_local"] = True, True

    _run_section(
        s, st,
        web_outcome=SearchOutcome(ok=True, results=[], provider="bocha_web_search", query="q"),
        local_outcome=SearchOutcome(ok=False, failure_reason="Milvus 服务不可用",
                                    provider="local_kb", query="q"),
    )

    attempted = _attempted(st, "litigation")
    assert "web_search" in attempted, "成功的那一路仍应记录"
    assert "local_kb" not in attempted, "失败的那一路不得搭便车"
    assert len(st["search_failures"]) == 1


def test_local_search_unavailable_is_failure_not_empty():
    s = _scout()
    s.milvus_service = None
    out = asyncio.run(s._execute_local_search("任意查询"))
    assert not out.ok, "本地库不可用是故障，不是『知识库里没有相关材料』"
    assert out.provider == "local_kb"


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
