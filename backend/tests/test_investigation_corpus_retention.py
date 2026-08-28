# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
调查层语料留存：去重要落到分片粒度，丢弃要留痕（BC-75）

## 这一轮在钉什么

阶段 2 观察期第一次三主体齐跑，两处刻意埋设的矛盾一处都没触发规则 1。
查下去发现留存那一步用 `(title, url)` 去重——**本地知识库的所有分片
天然共享 title 与 url**，它们本来就出自同一份文档。

于是一份 7 页的材料只有 1 页进了模型视野，实测：

    检索到 7 条 → 去重键 (title,url) 的不同取值数：1

这一个 bug 解释了三个主体的全部结果：泰锐 5 条发现全来自同一页；
mock002 的 13 页只进了 1 页（恰好是报表注释，于是 0 条发现）；
case01 是**多个不同文档**、title 各异，因此没被压缩，产出 17 条。

## 它为什么藏得住

丢弃**没有计数、没有留痕**，从外部看就是"模型没找到东西"——
与 BC-74 同一家族：一个失败伪装成一个正常的空结果（BC-51）。

还有一个放大效应：这个 bug 对 RAG 语料是毁灭性的、对网页检索几乎无害
（网页结果的 url 天然各异），**刚好绕开了最容易被注意到的路径**。

运行：cd backend && python -m pytest tests/test_investigation_corpus_retention.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.scout import (  # noqa: E402
    INVESTIGATION_CORPUS_LIMIT, INVESTIGATION_INPUT_CHAR_BUDGET,
    INVESTIGATION_EXCERPT_CHARS,
    DeepScout, _corpus_key,
)


def _scout():
    return DeepScout(llm_api_key="x", llm_base_url="http://127.0.0.1:1/v1",
                     search_api_key="")


def _chunks(n, doc_id="doc-1", title="材料包.txt"):
    """同一份文档的 n 个分片——**title 与 url 完全相同**，这是常态。"""
    return [{
        "title": title,
        "url": f"local://kb/mock/{doc_id}",
        "doc_id": doc_id,
        "chunk_index": i,
        "site_name": "本地知识库",
        "summary": f"第 {i} 页正文内容，各页各不相同。",
    } for i in range(n)]


def _state():
    return {"due_diligence_mode": True, "raw_sources": []}


def _retain(results, state=None):
    state = state if state is not None else _state()
    _scout()._retain_corpus_for_investigation(state, results, "sec_1")
    return state


# ------------------------------------------------- 一、分片不得被压成一片

def test_all_chunks_of_one_document_are_retained():
    """本条的直接回归：7 个分片必须留下 7 条，不是 1 条。"""
    state = _retain(_chunks(7))
    assert len(state["raw_sources"]) == 7, "同一文档的分片被去重压掉了"


def test_the_old_key_would_have_collapsed_them():
    """把成因也钉住：旧键在这批输入上确实只有一个取值。

    没有这条，上一条通过时读者无从知道它防的是什么。
    """
    results = _chunks(7)
    old_keys = {(r["title"], r["url"]) for r in results}
    new_keys = {_corpus_key(r) for r in results}
    assert len(old_keys) == 1, "夹具没有复现出成因"
    assert len(new_keys) == 7


def test_genuinely_duplicate_chunks_are_still_deduped():
    """反面：真正重复的分片仍要去掉。

    只钉"别去重"很容易写出一个完全不去重的实现——
    多个检索词命中同一分片是常态，重复送进模型是浪费也是噪声。
    """
    results = _chunks(3) + _chunks(3)
    state = _retain(results)
    assert len(state["raw_sources"]) == 3


def test_web_results_without_chunk_identity_fall_back_to_url():
    """网页结果没有 doc_id/chunk_index，退回 url + 摘要前缀。

    **不得退回 (title, url)**——那正是本条 bad case 的成因。
    """
    web = [{"title": "同名标题", "url": "https://a.example/1", "summary": "甲内容"},
           {"title": "同名标题", "url": "https://a.example/2", "summary": "乙内容"},
           {"title": "同名标题", "url": "https://a.example/1", "summary": "甲内容"}]
    state = _retain(web)
    assert len(state["raw_sources"]) == 2


# ------------------------------------------------- 二、丢弃必须可见

def test_duplicate_drops_are_recorded():
    """丢弃要计数。BC-75 之所以能藏住，就是因为去重不计数。"""
    state = _retain(_chunks(3) + _chunks(3))
    drops = state["investigation_corpus_drops"]
    assert drops and drops[0]["dropped_duplicate"] == 3
    assert drops[0]["retrieved"] == 6
    assert drops[0]["retained"] == 3


def test_over_limit_drops_are_recorded_separately():
    """超出上界的丢弃与重复丢弃要分开记。

    两者的处置完全不同：前者要调上界或收窄检索，后者是正常去重。
    合并成一个数就分不出该动哪里。
    """
    state = _retain(_chunks(INVESTIGATION_CORPUS_LIMIT + 5))
    drops = state["investigation_corpus_drops"]
    assert drops[0]["dropped_over_limit"] == 5
    assert drops[0]["dropped_duplicate"] == 0
    assert len(state["raw_sources"]) == INVESTIGATION_CORPUS_LIMIT


def test_no_drop_no_record():
    """没有丢弃就不留痕——恒定出现的留痕不携带信息（BC-18 的形态）。"""
    state = _retain(_chunks(3))
    assert state.get("investigation_corpus_drops") in (None, [])


# ------------------------------------------------- 三、边界

def test_non_due_diligence_mode_retains_nothing():
    """普通研究流程不走调查层，不该为它留语料。"""
    state = {"due_diligence_mode": False, "raw_sources": []}
    _retain(_chunks(5), state)
    assert state["raw_sources"] == []


def test_chunk_index_zero_is_not_treated_as_missing():
    """`chunk_index=0` 是合法分片号，不得被当成缺失而退回网页分支。

    这是 `if doc_id and chunk` 这类写法的经典陷阱：0 是假值。
    """
    key = _corpus_key({"doc_id": "d", "chunk_index": 0, "url": "u"})
    assert key[0] == "chunk", "chunk_index=0 被当成了缺失"


# ------------------------------------------------- 四、总量预算（BC-77）


def _long_chunks(n, chars=1200, doc_id="doc-long"):
    """满长度分片——生产里 40 条 × 1200 字正压在模型输入上限上。"""
    return [{
        "title": "材料包.txt",
        "url": f"local://kb/mock/{doc_id}",
        "doc_id": doc_id,
        "chunk_index": i,
        "summary": f"第{i}片" + "内" * (chars - 4),
    } for i in range(n)]


def test_total_input_stays_within_the_model_budget():
    """只限条数不限总量，就没有任何东西为「送多少字」负责。

    ⚠️ **这段注释原来写的成因是错的，保留纠正而不是删掉。**

    原文写：「40 条 × 1200 字 ≈ 47000 字，中文近似 1 字 1 token，
    撞上 30720 的输入硬上限」。两处都不对：

    1. 那次 400 来自 **qwen-max**——探针写 `CodeWizard(key, url)`
       没传 model，撞上默认值。生产走 `deepseek-v4-flash`，
       实测 400,000 字仍然通过。
    2. 「1 字 1 token」也是错的：实测 qwen-max 上财报文本 1.34 字/token、
       规整散文 2.02 字/token。**同一个比例在不同内容上差 50%。**

    这条断言本身仍然成立且有意义——总量要有人负责，
    只是它守的是一道安全网，不是一条撞过的红线。
    """
    state = _retain(_long_chunks(INVESTIGATION_CORPUS_LIMIT))
    total = sum(len(s["summary"]) for s in state["raw_sources"])
    assert total <= INVESTIGATION_INPUT_CHAR_BUDGET, (
        f"送进模型的正文 {total} 字，超过预算 "
        f"{INVESTIGATION_INPUT_CHAR_BUDGET} 字")


def test_生产常量下总量预算不再先于条数上界生效():
    """**这条断言的方向与本文件原来的版本相反，是有意反过来的。**

    BC-77 当时把预算定成 24000，依据是一次实测 400：

        Range of input length should be [1, 30720]

    但那次探针写的是 `CodeWizard(key, url)`——没传 model，撞上默认值
    `"qwen-max"`。生产这次调用走 `config.agents.wizard.model`，
    即 `deepseek-v4-flash`，实测 400,000 字仍然通过。

    24000 因此是在为一个生产从未遇到的限制服务，代价是八章清单里
    七章的语料被整体丢弃（BC-78）。预算提到 50000（≥ 40×1200）之后，
    **总量不再是闸门，条数才是**。

    这条测试的作用是：如果哪天有人把预算调回去，能立刻看见契约变了。
    """
    state = _retain(_long_chunks(INVESTIGATION_CORPUS_LIMIT))
    assert len(state["raw_sources"]) == INVESTIGATION_CORPUS_LIMIT, (
        "满长度分片下应当由条数上界收口，而不是总量预算")
    assert INVESTIGATION_INPUT_CHAR_BUDGET >= (
        INVESTIGATION_CORPUS_LIMIT * INVESTIGATION_EXCERPT_CHARS), (
        "预算低于「条数 × 每条长度」，它就又变回闸门了")


def test_budget_drops_are_recorded_separately_from_count_drops(monkeypatch):
    """总量丢弃与条数丢弃要分开记。

    两者的处置不同：前者该调总量预算或摘录长度，后者该调条数上界。
    合并成一个数就分不出该动哪里（与 BC-75 同一条纪律）。

    ⚠️ 生产常量下这条路径**走不到**（见上一条）。走不到不等于可以不对：
    常量还会再动。所以把预算压回会生效的位置，继续钉住记账行为。
    """
    import service.deep_research_v2.agents.scout as scout_mod
    monkeypatch.setattr(scout_mod, "INVESTIGATION_INPUT_CHAR_BUDGET", 12000)

    state = _retain(_long_chunks(INVESTIGATION_CORPUS_LIMIT))
    # 前提：这个设置下预算确实先生效，否则下面测的是条数上界
    assert len(state["raw_sources"]) < INVESTIGATION_CORPUS_LIMIT, (
        "压低预算之后仍然是条数先到顶，夹具没复现出成因")
    drop = state["investigation_corpus_drops"][0]
    assert drop["dropped_over_budget"] > 0
    assert drop["dropped_duplicate"] == 0
    assert drop["retained_chars"] <= 12000


def test_the_budget_does_not_reorder_material_by_length():
    """总量到顶时**整批停下**，不得跳过长片去捡后面的短片。

    跳过会让送进模型的材料按**长度**而非**相关性**挑选——
    检索给出的顺序是相关性顺序，绕过它等于静默改变抽取结果的构成，
    而且这种改变在留痕里看不出来。
    """
    # 夹具要卡在一个特定位置：**下一片长的进不来，但那片短的进得来**。
    # 否则 break 与 continue 表现相同，这条用例什么都区分不了
    # （第一版就是这样，短片本来就还在预算内）。
    size = 1150
    fill = INVESTIGATION_INPUT_CHAR_BUDGET // size          # 填到接近预算
    results = _long_chunks(fill + 1, chars=size) + [{
        "title": "材料包.txt", "url": "local://kb/mock/doc-long",
        "doc_id": "doc-long", "chunk_index": 99, "summary": "很短的一片",
    }]
    used = fill * size
    assert used + size > INVESTIGATION_INPUT_CHAR_BUDGET, "下一片长的应当超预算"
    assert used + 5 <= INVESTIGATION_INPUT_CHAR_BUDGET, "那片短的应当仍在预算内"

    state = _retain(results)
    kept = {s["summary"][:4] for s in state["raw_sources"]}
    assert "很短的一片"[:4] not in kept, "跳过长片捡了短片，材料按长度被重排了"


def test_short_material_is_not_dropped_by_the_budget():
    """反面：正常长度的材料不得被预算误伤。

    只钉"别超预算"，很容易写出一个把大部分材料都丢掉的实现。
    """
    state = _retain(_chunks(10))
    assert len(state["raw_sources"]) == 10
    assert not [d for d in state.get("investigation_corpus_drops") or []
                if d["dropped_over_budget"]]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
