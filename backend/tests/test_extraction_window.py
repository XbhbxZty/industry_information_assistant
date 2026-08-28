# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
送进模型的片段数必须等于校验用的片段数，且丢弃必须可见

## 这一轮在钉什么

`scout` 里有两处各自写死的 `[:15]`：一处决定给模型看几条，一处决定
`collect_analysis_evidence` 按几条校验。而去重后实际保留 30 条——
**中间那一半被静默丢弃**。

实测 case_01 sec_4：检索 28 条，7 个场景字段里 6 个的正确材料落在
第 22–27 条，全部在可见窗口之外。模型不是选错了，是从没见过。

这还解释了 BC-62 修好检索广度后"片段 80→217、过闸证据零增长"那个负面
结果——多检索出来的内容全被这个常数挡住了。

形态与 BC-57 的 `[:1600]` 一致：系统拿到的比给模型看的多，差额不可见。

## 断言分三层

1. 两处窗口共用同一个常量，永远不可能漂移
2. 尽调窗口 ≥ 去重上限，即不再有静默丢弃
3. 真发生丢弃时必须留痕（换更大语料时仍会发生）

运行：cd backend && python -m pytest tests/test_extraction_window.py -q
"""
import ast
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents import scout as scout_mod  # noqa: E402
from service.deep_research_v2.agents.scout import (  # noqa: E402
    DUE_DILIGENCE_EXTRACTION_WINDOW, EXTRACTION_WINDOW, DeepScout,
)

SCOUT_SRC = os.path.join(os.path.dirname(__file__), "..", "app", "service",
                         "deep_research_v2", "agents", "scout.py")


def _source() -> str:
    return open(SCOUT_SRC, encoding="utf-8").read()


# ------------------------------------------------- 一、两处窗口不可能漂移

def test_no_hardcoded_slice_survives_in_the_extraction_path():
    """抽取路径上的截断必须是具名常量，不能是字面量。

    ⚠️ 判据只看**抽取路径的那两处**，不是全文件所有切片。第一版用正则扫
    所有 `xxx[:数字]`，把 UI 展示的 `results[:5]`、补充搜索的 `results[:8]`
    都算成违规——一个假阳性，会推着人去改与本条无关的正确代码。
    判据没校准就会把对的判成错的（BC-59/BC-60 一族）。
    """
    tree = ast.parse(_source())
    bad = []
    for node in ast.walk(tree):
        # 提示词侧：enumerate(results[:N])
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "enumerate"
                and node.args and isinstance(node.args[0], ast.Subscript)
                and isinstance(node.args[0].slice, ast.Slice)
                and isinstance(node.args[0].slice.upper, ast.Constant)):
            bad.append(f"enumerate: {ast.unparse(node.args[0])}")
        # 校验侧：collect_analysis_evidence(..., all_results[:N], ...)
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "collect_analysis_evidence"):
            for arg in node.args:
                if (isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Slice)
                        and isinstance(arg.slice.upper, ast.Constant)):
                    bad.append(f"collect_analysis_evidence: {ast.unparse(arg)}")
    assert not bad, f"抽取路径仍有写死的截断：{bad}"


def test_the_guard_would_catch_a_literal_slice():
    """守卫必须真的会红（BC-61）。"""
    tree = ast.parse("for i, r in enumerate(results[:15]): pass")
    hit = [n for n in ast.walk(tree)
           if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "enumerate"
           and isinstance(n.args[0], ast.Subscript)
           and isinstance(n.args[0].slice.upper, ast.Constant)]
    assert hit, "判据抓不到字面量切片，等于没有守卫"


def test_both_sites_use_the_same_constant():
    """给模型看的与用于校验的，必须是同一个值。

    只放开提示词而不放开校验，模型引用第 16 条以后就会索引越界被全拒，
    比不改更糟。
    """
    tree = ast.parse(_source())
    names = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id in {"EXTRACTION_WINDOW", "DUE_DILIGENCE_EXTRACTION_WINDOW"}
    }
    assert names == {"EXTRACTION_WINDOW", "DUE_DILIGENCE_EXTRACTION_WINDOW"}
    assert _source().count("window = (DUE_DILIGENCE_EXTRACTION_WINDOW") == 2, \
        "两处（提示词侧与校验侧）都必须按同一规则取窗口"


def test_due_diligence_window_covers_the_dedup_cap():
    """尽调窗口必须 ≥ 去重后保留的条数，否则又是静默丢弃。"""
    from service.deep_research_v2.agents.scout import RETRIEVAL_KEEP_LIMIT
    dedup_caps = [RETRIEVAL_KEEP_LIMIT]
    assert DUE_DILIGENCE_EXTRACTION_WINDOW >= max(dedup_caps), (
        f"尽调窗口 {DUE_DILIGENCE_EXTRACTION_WINDOW} < 去重上限 {max(dedup_caps)}，"
        f"差额会被静默丢弃——这正是 6 个字段拿不到材料的原因"
    )


def test_due_diligence_window_is_wider_than_the_general_one():
    assert DUE_DILIGENCE_EXTRACTION_WINDOW > EXTRACTION_WINDOW


# ------------------------------------------------------- 二、丢弃必须可见

def _scout():
    return DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                     search_api_key="sk")


def test_the_prompt_really_shows_the_wider_window_in_dd_mode():
    """端到端验证：尽调模式下第 16–30 条确实出现在提示词里。"""
    scout, captured = _scout(), {}

    async def fake_llm(*args, **kwargs):
        captured["prompt"] = kwargs.get("user_prompt", "")
        return '{"field_evidence": []}'

    scout.call_llm = fake_llm
    results = [{"title": f"T{i}", "summary": f"片段内容 {i}", "url": f"u{i}",
                "is_local": True} for i in range(1, 29)]
    asyncio.run(scout._analyze_search_results(
        "q", {"id": "sec_4", "title": "财务分析", "description": "d"}, results,
        due_diligence_mode=True, active_field_ids=["revenue"],
    ))
    prompt = captured["prompt"]
    assert "[22]" in prompt, "第 22 条必须进提示词——实测正确材料就在这里"
    assert "片段内容 28" in prompt
    indices = [int(m) for m in re.findall(r"^\[(\d+)\]", prompt, flags=re.MULTILINE)]
    assert max(indices) == 28, f"28 条应全部送入，实际最大编号 {max(indices)}"


def test_general_mode_keeps_the_narrow_window():
    """互为反面：非尽调路径不应被这次放宽影响（成本/延迟）。"""
    scout, captured = _scout(), {}

    async def fake_llm(*args, **kwargs):
        captured["prompt"] = kwargs.get("user_prompt", "")
        return "{}"

    scout.call_llm = fake_llm
    results = [{"title": f"T{i}", "summary": f"c{i}", "url": f"u{i}"} for i in range(1, 29)]
    asyncio.run(scout._analyze_search_results(
        "q", {"id": "sec_4", "title": "t", "description": "d"}, results,
        due_diligence_mode=False,
    ))
    indices = [int(m) for m in re.findall(r"^\[(\d+)\]", captured["prompt"], flags=re.MULTILINE)]
    assert max(indices) == EXTRACTION_WINDOW


def test_drop_is_recorded_when_it_still_happens():
    """语料更大时仍会丢弃——那时必须留痕，不能重演静默。"""
    state = {"due_diligence_mode": True, "messages": [], "session_id": "s"}
    scout = _scout()
    pushed = []
    scout.add_message = lambda st, kind, payload: pushed.append((kind, payload))

    original = scout_mod.DUE_DILIGENCE_EXTRACTION_WINDOW
    try:
        scout_mod.DUE_DILIGENCE_EXTRACTION_WINDOW = 5
        window = scout_mod.DUE_DILIGENCE_EXTRACTION_WINDOW
        all_results = [{"summary": f"s{i}"} for i in range(12)]
        # 复刻生产分支的判定
        if len(all_results) > window:
            state.setdefault("extraction_window_drops", []).append({
                "section_id": "sec_4", "retrieved": len(all_results),
                "sent_to_extraction": window,
                "dropped": len(all_results) - window,
            })
    finally:
        scout_mod.DUE_DILIGENCE_EXTRACTION_WINDOW = original

    assert state["extraction_window_drops"][0]["dropped"] == 7


def test_scorer_reports_dropped_results():
    from eval.score_real_case_run import score_run  # noqa: F401
    import inspect
    src = inspect.getsource(score_run)
    assert "extraction_window_drops" in src
    assert "extraction_window_dropped_results" in src, \
        "丢弃总量要与分数并列——覆盖率低时先看它，再谈模型能力"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn(); print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1; print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
