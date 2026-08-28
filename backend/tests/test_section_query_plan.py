# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
检索广度不得依赖模型的标点习惯（BC-62）

## 这一轮在钉什么

`architect._convert_flat_to_outline` 原来写的是
`search_queries: [flat_result.get(f"sec_{i}_query")]`——**永远只有一个元素**；
而 Scout 侧 `expand_local_search_queries` 又按 `；` 把它拆开。于是"这一章发几次
检索"由模型的标点习惯隐式决定，**而提示词从未要求过分号**：

    deepseek-v3.2      分号连写   → 25 条查询 → 178 个片段
    deepseek-v4-flash  一个长串   →  8 条查询 →  80 个片段

两者都完全符合当时的提示词。一个 `str` 在承载 `list[str]`，转换隐式且无人校验
——与 BC-55 同族（类型没有表达真实需求）。

更糟的是**完全不可观测**：`expand_local_search_queries(queries, limit=6)` 只有
上限没有下限，没有任何一层说过"这一章只拿到 1 条查询"。少检索 55%，而
`status` 照样 `completed`、`search_failures` 是空的——静默降级（BC-02 一族）。

## 断言分三层

1. **契约**：解析器优先读数组，不再靠标点
2. **兜底**：旧格式仍可工作，但兜底这件事必须被记录（契约没被遵守）
3. **可观测**：低于下限要留痕、要推事件、要能被评测器看见；
   但**不得**降级为故障——查询偏少仍能产出证据，与"检索没查成"性质不同，
   混同会让附录里的故障表失去意义

运行：cd backend && python -m pytest tests/test_section_query_plan.py -q
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.architect import ChiefArchitect  # noqa: E402
from service.deep_research_v2.agents.scout import (  # noqa: E402
    MIN_SECTION_QUERIES, DeepScout, expand_local_search_queries,
)
from service.deep_research_v2.state import create_initial_state  # noqa: E402


def _scout() -> DeepScout:
    return DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                     search_api_key="sk")


def _state():
    return create_initial_state("尽调", "test-session", due_diligence=True)


# ------------------------------------------------------------------ 一、契约

def test_parser_reads_an_explicit_query_array():
    flat = {
        "sec_1_title": "企业基本情况",
        "sec_1_queries": ["宁德时代 工商登记", "宁德时代 参保人数", "宁德时代 经营范围"],
    }
    assert ChiefArchitect._section_queries(flat, 1) == [
        "宁德时代 工商登记", "宁德时代 参保人数", "宁德时代 经营范围"
    ]


def test_outline_conversion_preserves_every_query():
    """回归本条的直接成因：原实现把列表压成了 1 个元素。"""
    architect = object.__new__(ChiefArchitect)
    flat = {
        "sec_1_title": "企业基本情况", "sec_1_desc": "d",
        "sec_1_queries": ["q1 一件事", "q2 另一件事", "q3 第三件事"],
    }
    outline = architect._convert_flat_to_outline(flat)["outline"]
    assert len(outline) == 1
    assert outline[0]["search_queries"] == ["q1 一件事", "q2 另一件事", "q3 第三件事"], \
        "章节的检索词列表不得被压成一条——那正是 178→80 的成因"


def test_blank_and_non_string_entries_are_dropped_not_retained():
    flat = {"sec_2_title": "股权结构", "sec_2_queries": ["有效检索词", "", "   ", None]}
    assert ChiefArchitect._section_queries(flat, 2) == ["有效检索词"]


# ------------------------------------------------------------------ 二、兜底

def test_legacy_single_query_key_still_works():
    """旧检查点与旧模型仍必须能跑。"""
    flat = {"sec_3_title": "经营状况", "sec_3_query": "主营业务；客户集中度；产能"}
    assert ChiefArchitect._section_queries(flat, 3) == ["主营业务；客户集中度；产能"], \
        "旧键仍要被接受，交给下游确定性拆分，不在解析器里猜"


def test_model_writing_the_array_as_a_string_is_passed_to_the_splitter():
    flat = {"sec_4_title": "财务分析", "sec_4_queries": "营收；净利润；现金流"}
    assert ChiefArchitect._section_queries(flat, 4) == ["营收；净利润；现金流"]
    assert expand_local_search_queries(["营收；净利润；现金流"]) == ["营收", "净利润", "现金流"]


def test_two_character_chinese_terms_survive_the_split():
    """最小片段长度必须按中文校准（BC-60 同族）。

    原来的 `>= 3` 会静默丢掉 `营收`、`存货`、`担保`——`存货` 本身就是固定清单
    的字段关键词。一个判据里的数字按英文单词的直觉写，在中文语料上就是
    系统性丢查询，而且不报错。
    """
    assert expand_local_search_queries(["存货；担保；营收"]) == ["存货", "担保", "营收"]
    # 1 个字符仍丢弃：那是拆分碎片，不是查询
    assert expand_local_search_queries(["存货；的；担保"]) == ["存货", "担保"]


def test_title_is_the_last_resort_never_an_empty_query_list():
    flat = {"sec_5_title": "司法与合规风险"}
    assert ChiefArchitect._section_queries(flat, 5) == ["司法与合规风险"], \
        "宁可用标题检索，也不能返回空列表——空列表会让这一章一次检索都不发"


# -------------------------------------------------------------- 三、可观测

def test_shortfall_is_recorded_and_emitted_when_queries_are_too_few():
    """这是本条真正的防线：下次换模型时不再静默。"""
    scout, state = _scout(), _state()
    pushed = []
    scout.add_message = lambda st, kind, payload: pushed.append((kind, payload))

    atomic = scout._plan_section_queries(
        state, {"id": "sec_4", "title": "财务分析"},
        ["宁德时代 财务报表 营收 净利润 资产负债率 应收账款 经营性现金流"],
    )
    assert len(atomic) == 1
    shortfalls = state["query_plan_shortfalls"]
    assert len(shortfalls) == 1
    assert shortfalls[0]["section_id"] == "sec_4"
    assert shortfalls[0]["atomic_queries"] == 1
    assert shortfalls[0]["min_expected"] == MIN_SECTION_QUERIES
    plans = [payload for kind, payload in pushed if kind == "section_query_plan"]
    assert plans and plans[0]["below_minimum"] is True, "不足必须推事件，不能只写日志"


def test_sufficient_queries_leave_no_shortfall():
    """互为反面：够了就不该留痕，否则这张表会变成永远亮的告警。"""
    scout, state = _scout(), _state()
    scout.add_message = lambda st, kind, payload: None
    atomic = scout._plan_section_queries(
        state, {"id": "sec_1", "title": "企业基本情况"},
        ["宁德时代 工商登记", "宁德时代 参保人数", "宁德时代 经营范围"],
    )
    assert len(atomic) == 3
    assert state.get("query_plan_shortfalls") == []


def test_split_fallback_is_reported_even_when_the_count_is_fine():
    """兜底成功也要留痕：它意味着契约没被遵守，广度正依赖这次拆分。"""
    scout, state = _scout(), _state()
    pushed = []
    scout.add_message = lambda st, kind, payload: pushed.append((kind, payload))
    scout._plan_section_queries(
        state, {"id": "sec_2", "title": "股权结构"},
        ["前十名股东；实际控制人；股权质押"],
    )
    plan = [p for k, p in pushed if k == "section_query_plan"][0]
    assert plan["atomic_queries"] == 3
    assert plan["split_fallback_used"] is True
    assert plan["planned_queries"] == 1
    assert state.get("query_plan_shortfalls") == [], "数量够了就不算不足"


def test_shortfall_is_not_a_search_failure():
    """查询偏少 ≠ 检索没查成。

    混同会让证据附录里的故障表失去意义——那张表的每一行都要求读者
    "线下补充核查"，塞进一堆"查询偏少"会让真正的故障被淹没（BC-51 的反面）。
    """
    scout, state = _scout(), _state()
    scout.add_message = lambda st, kind, payload: None
    scout._plan_section_queries(state, {"id": "sec_7", "title": "舆情扫描"},
                               ["宁德时代 负面新闻 监管处罚 行业风险"])
    assert state["query_plan_shortfalls"], "前提：确实记了不足"
    assert state.get("search_failures") == [], "不得写进检索故障"
    assert state.get("section_failures") == [], "也不得写进章节故障"
    assert state.get("errors") == [], "不阻断完成态——它仍能产出证据"


def test_the_two_measured_model_behaviours_are_now_distinguishable():
    """把 BC-62 的实测输入直接喂进来，确认两者现在可区分。

    左边是 deepseek-v3.2 实际写出的形态，右边是 deepseek-v4-flash 的。
    改造前两者在任何一层都看不出差别；现在必须看得出。
    """
    scout = _scout()
    scout.add_message = lambda st, kind, payload: None

    old_state = _state()
    old = scout._plan_section_queries(
        old_state, {"id": "sec_1", "title": "企业基本情况"},
        ["宁德时代新能源科技股份有限公司 工商登记信息",
         "宁德时代 参保人数 2022 2023 2024", "宁德时代 经营范围"],
    )
    new_state = _state()
    new = scout._plan_section_queries(
        new_state, {"id": "sec_1", "title": "企业基本情况"},
        ["宁德时代 工商信息 成立日期 注册资本 经营范围 参保人数 登记状态"],
    )

    assert len(old) == 3 and len(new) == 1
    assert old_state.get("query_plan_shortfalls") == []
    assert len(new_state["query_plan_shortfalls"]) == 1, \
        "关键词塞成一条必须被记录——否则检索广度腰斩仍然完全静默"


def test_section_eight_is_exempt_from_the_query_minimum():
    """规格例外不得产出假告警。

    第 8 章「风险汇总与授信建议」不预设检索词，它消费前七章的核查结果——
    提示词里写明可以只有 1 条。把一刀切的下限套上去，B′ 轮就报了一次假告警。
    代价不是这一条噪声：一张总是亮着的告警表，读者很快学会略过它，
    真正的不足也跟着被忽略。判据要对规格校准（BC-60 同族）。
    """
    scout, state = _scout(), _state()
    pushed = []
    scout.add_message = lambda st, kind, payload: pushed.append((kind, payload))
    scout._plan_section_queries(state, {"id": "sec_8", "title": "风险汇总与授信建议"},
                               ["宁德时代 信用风险 综合"])
    assert state.get("query_plan_shortfalls") == [], "第 8 章只有 1 条是规格允许的"
    plan = [p for k, p in pushed if k == "section_query_plan"][0]
    assert plan["minimum_exempt"] is True, "豁免要如实写进计划，而不是悄悄不报"


def test_non_exempt_section_with_the_same_count_still_reports():
    """互为反面：豁免只对第 8 章生效，不能变成对所有章节放水。"""
    scout, state = _scout(), _state()
    scout.add_message = lambda st, kind, payload: None
    scout._plan_section_queries(state, {"id": "sec_4", "title": "财务分析"},
                               ["宁德时代 财务综合"])
    assert len(state["query_plan_shortfalls"]) == 1


def test_scorer_surfaces_shortfalls_without_scoring_them(tmp_path):
    """评测器要报告它，但**不扣分、不设闸门**。

    它不是质量缺陷而是实验条件缺陷：这一章只发了一次检索，"覆盖率低"就无法
    区分是资料里没有还是根本没去查。拿它扣分会把归因问题伪装成质量问题。
    """
    import json
    from pathlib import Path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from eval import score_real_case_run

    run_dir = Path(tmp_path) / "run"
    run_dir.mkdir()
    final_event = {
        "type": "research_complete", "final_report": "报告" * 600,
        "risk_assessment": {"requires_human_review": True}, "errors": [],
        "section_failures": [],
        "query_plan_shortfalls": [{"section_id": "sec_4", "atomic_queries": 1,
                                   "min_expected": 3}],
    }
    (run_dir / "result.json").write_text(json.dumps({
        "case_id": "case_01", "session_id": "u", "status": "completed",
        "research_cutoff": "2025-05-31", "search_web": False, "search_local": True,
        "kb_scope": [{"kb_id": "case_01"}], "reference_layer_read": False,
        "post_cutoff_layer_read": False, "final_event": final_event,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.md").write_text(final_event["final_report"], encoding="utf-8")
    (run_dir / "events.jsonl").write_text(json.dumps({
        "type": "search_results", "as_of": "2025-05-31",
        "content": {"searchType": "local", "results": [{
            "url": "local://kb/case_01/d",
            "snippet": "[case_id=case_01; source_id=S001; locator=page:1] t"}]},
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    score = score_real_case_run.score_run(run_dir)
    assert score["metrics"]["query_plan_shortfall_sections"] == 1
    assert score["query_plan_shortfalls"][0]["section_id"] == "sec_4"
    assert "query_plan" not in " ".join(score["gates"]), "不得成为闸门"
    assert "retrieved_result_count" in score["metrics"], \
        "检索总量要与分数并列——跨轮次比分数前先对齐它"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            if fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
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
