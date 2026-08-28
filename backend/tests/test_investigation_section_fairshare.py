# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""调查层语料按章分配（BC-78）

## 这条测试要挡住的具体缺陷

批次 2 实测，case01 一轮的逐章留存：

    sec_1   检索 22   累计留存 21   累计字 23922   超预算丢 1
    sec_2   检索 23   累计留存 21   累计字 23922   超预算丢 23
    ...
    sec_8   检索 10   累计留存 21   累计字 23922   超预算丢 10

**第一章吃光全部额度，后面七章共 166 条检索结果一条没进模型。**
八章清单，B 层只看得见一章。

## 测试必须先证明自己有能力失败

BC-77 那条「不得按长度重排」的守卫第一次是空过的——夹具里所有分片
等长，正确实现与错误实现结果完全一样。所以这里每条断言之前，
先断言**先到先得的实现确实会在这个夹具上露馅**。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from service.deep_research_v2.agents import scout as scout_mod  # noqa: E402
from service.deep_research_v2.agents.scout import DeepScout  # noqa: E402


class _Shim:
    """只借留存这一个未绑定方法——构造真 DeepScout 需要 key 与网络配置，
    而留存函数不碰 self 上的任何东西。"""

    def retain(self, state, results, section_id):
        DeepScout._retain_corpus_for_investigation(self, state, results, section_id)


def _results(section: str, n: int, chars: int = 1200) -> List[Dict[str, Any]]:
    return [{"doc_id": f"{section}-doc", "chunk_index": i,
             "title": f"{section} 第{i}片", "url": f"local://{section}",
             "summary": "材" * chars} for i in range(n)]


def _state(sections: int = 8) -> Dict[str, Any]:
    return {
        "due_diligence_mode": True,
        "raw_sources": [],
        "outline": [{"section_id": f"sec_{i}"} for i in range(1, sections + 1)],
    }


def _run(state, sections: int, per_section: int) -> None:
    shim = _Shim()
    for i in range(1, sections + 1):
        shim.retain(state, _results(f"sec_{i}", per_section), f"sec_{i}")


def _by_section(state) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in state["raw_sources"]:
        out[item["section_id"]] = out.get(item["section_id"], 0) + 1
    return out


# --------------------------------------------------------------- 夹具自检


def test_夹具能让先到先得的实现露馅():
    """先证明这个夹具有鉴别力，再用它去断言正确行为。

    八章各检索 30 条，而条数上界是 40。**先到先得会让第一章拿满 40，
    后面七章一条不剩**——如果这个前提不成立，下面所有断言都是空过的。
    """
    assert scout_mod.INVESTIGATION_CORPUS_LIMIT < 8 * 30, (
        "夹具失效：每章 30 条 × 8 章没有超过条数上界，先到先得不会露馅")
    # 单章检索量必须能一次吃满上界，否则测不出"吃光"这件事
    assert 30 >= scout_mod.INVESTIGATION_CORPUS_LIMIT / 2, (
        "夹具失效：单章 30 条吃不下上界的一半，饿死现象不会出现")


# --------------------------------------------------------------- 核心断言


def test_八章都必须分到语料():
    state = _state(8)
    _run(state, sections=8, per_section=30)
    counts = _by_section(state)
    missing = [f"sec_{i}" for i in range(1, 9) if counts.get(f"sec_{i}", 0) == 0]
    assert not missing, (
        f"这些章节一条语料都没进调查层：{missing}；"
        f"实际分布 {counts}——这正是 BC-78 的现象")


def test_第一章不得吃掉一半以上的额度():
    state = _state(8)
    _run(state, sections=8, per_section=30)
    counts = _by_section(state)
    first = counts.get("sec_1", 0)
    total = sum(counts.values())
    assert first <= total * 0.5, (
        f"sec_1 拿了 {first}/{total}，先到先得没有被修掉")


def test_前面章节少用时后面能多分():
    """自适应：前三章各只有 1 条，后面的章节应当分到更多而不是仍按均分。"""
    state = _state(8)
    shim = _Shim()
    for i in (1, 2, 3):
        shim.retain(state, _results(f"sec_{i}", 1), f"sec_{i}")
    for i in (4, 5, 6, 7, 8):
        shim.retain(state, _results(f"sec_{i}", 30), f"sec_{i}")
    counts = _by_section(state)
    assert counts.get("sec_4", 0) > 1, (
        f"前面章节让出的额度没有被后面用上：{counts}")
    assert sum(counts.values()) > 8, (
        f"总留存 {sum(counts.values())} 条，额度被白白浪费了")


def test_条数上界仍然生效():
    state = _state(8)
    _run(state, sections=8, per_section=30)
    assert len(state["raw_sources"]) <= scout_mod.INVESTIGATION_CORPUS_LIMIT, (
        "按章分配不得突破总条数上界——那是成本闸门")


# --------------------------------------------------------------- 留痕


def test_配额用完时本章的丢弃要记在本章名下():
    """按章分配之后，超额丢弃发生在**配额**上而不是全局上界上。

    这一条盯的是「记在谁名下」：丢弃必须挂在当章，否则排查时
    看不出是哪一章材料不足。
    """
    state = _state(8)
    shim = _Shim()
    shim.retain(state, _results("sec_1", 30), "sec_1")
    drops = state.get("investigation_corpus_drops") or []
    assert drops and drops[-1]["section_id"] == "sec_1"
    rec = drops[-1]
    assert rec["dropped_over_limit"] == 30 - rec["section_quota"], (
        f"丢弃条数应当是「检索数 - 本章配额」：{rec}")


def test_全局条数满时整章丢弃必须留痕():
    """BC-75 没堵完的那半：`room<=0` 原先直接 return，什么都不记。

    按章分配让这条路径难触发了，但**没有让它消失**——章节数多于
    大纲声明时（例如补查追加的章节）仍会走到。它必须记账。
    """
    state = _state(8)
    shim = _Shim()
    for i in range(1, 9):                       # 八章各自吃满配额
        shim.retain(state, _results(f"sec_{i}", 30), f"sec_{i}")

    # 先证明这个前提成立：上界确实已经满了，否则下面测的是另一条路径
    assert len(state["raw_sources"]) >= scout_mod.INVESTIGATION_CORPUS_LIMIT, (
        f"夹具失效：八章只留存了 {len(state['raw_sources'])} 条，"
        f"没到上界 {scout_mod.INVESTIGATION_CORPUS_LIMIT}，"
        f"`room<=0` 那条路径根本不会被走到")

    before = len(state.get("investigation_corpus_drops") or [])
    shim.retain(state, _results("sec_9", 20), "sec_9")   # 大纲之外的追加章节
    drops = state.get("investigation_corpus_drops") or []
    assert len(drops) > before, (
        "整章被丢弃却没有留下任何记录——外观与「这章没检索到东西」完全相同")
    last = drops[-1]
    assert last["section_id"] == "sec_9"
    assert last["dropped_over_limit"] == 20, f"丢弃条数记错了：{last}"
    assert "整章未进入调查层" in str(last.get("note") or ""), (
        f"留痕要说清这是整章丢弃，不是部分丢弃：{last}")


def test_留痕要能说清是分配所致还是材料所致():
    state = _state(8)
    _run(state, sections=8, per_section=30)
    drops = state.get("investigation_corpus_drops") or []
    assert drops, "应当有丢弃记录"
    rec = drops[0]
    for key in ("section_quota", "sections_remaining", "sections_total",
                "outline_available"):
        assert key in rec, (
            f"留痕缺少 {key}——没有它就说不清「这章只留 3 条」"
            f"是配额造成的还是材料只有 3 条")


def test_取不到大纲时不按猜的分母配额且退化可见():
    """**分母未知时不配额，这是有意的取舍，不是遗漏。**

    第一版写的是 `len(outline) or 8`——取不到大纲就假设八章。
    结果一个只有一章的运行只拿到 1/8 额度，另外 7/8 永远没人取，
    已有的五条单章留存测试全被饿死。

    流式分配没法回头补：第一章让出去的额度要不回来。所以没有分母时，
    任何猜测都会在某一侧出错（猜大饿死单章，猜小保护不了多章）。
    宁可退回先到先得**并把这件事记进留痕**，也不按猜的数去分。

    生产路径上大纲一定在——`_execute_deep_search` 本身就是按大纲
    逐章调的，BC-78 也正是在有大纲的情况下发生的。
    """
    state = {"due_diligence_mode": True, "raw_sources": []}   # 没有 outline
    shim = _Shim()
    shim.retain(state, _results("sec_1", 30), "sec_1")
    counts = _by_section(state)
    assert counts.get("sec_1") == 30, (
        f"分母未知时单章应当拿满，不该被一个猜出来的分母饿死：{counts}")

    # 但退化必须可见：留痕要能看出这一批没有走配额
    shim.retain(state, _results("sec_2", 30), "sec_2")
    drops = state.get("investigation_corpus_drops") or []
    assert drops, "有丢弃就必须留痕"
    assert any(r.get("outline_available") is False for r in drops), (
        "「本次没有按章配额」这件事必须在留痕里可见，否则它会静默退化")


# --------------------------------------------------------------- 预算


def test_总量预算不再是容量约束():
    """预算是安全网，不该在正常语料上生效。

    24000 那个值是按 qwen-max 的 30720 token 上限定的，而生产这次调用
    走 deepseek-v4-flash（实测 400,000 字仍通过）。
    """
    assert scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET >= (
        scout_mod.INVESTIGATION_CORPUS_LIMIT
        * scout_mod.INVESTIGATION_EXCERPT_CHARS), (
        "预算低于「条数上界 × 每条长度」，它就又变成了真正的闸门，"
        "而条数分配将不再决定实际构成")


@pytest.mark.parametrize("sections", [1, 3, 8, 20])
def test_不同章节数都不饿死(sections):
    state = _state(sections)
    _run(state, sections=sections, per_section=30)
    counts = _by_section(state)
    if sections <= scout_mod.INVESTIGATION_CORPUS_LIMIT:
        assert len(counts) == sections, (
            f"{sections} 章里只有 {len(counts)} 章拿到语料：{counts}")
