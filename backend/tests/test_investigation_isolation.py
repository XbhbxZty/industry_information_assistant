# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
A 层 / B 层物理隔离（双轨产出计划阶段 1 的验收条件）

## 这一轮在钉什么

阶段 1 的承诺是"把图表和知识图谱接回来，**一道闸门都不松**"。
这个承诺只有一种检验方式：**证明 B 层写不到 A 层**。

计划里写下的验收标准是：

> 同一主体跑两遍，A 层的等级、额度、核实率**逐位不变**；
> 报告新增 B 层章节；证据附录不含任何 B 层内容。

前半句是行为断言，后半句是结构断言，这里两种都做。

## 为什么结构断言必须走 AST

这个项目已经四次被"守卫误伤文档"咬到：字符串匹配的守卫先后命中过
docstring 里的示例、注释里引用的旧写法、以及**修复说明本身**。
每次的正确修法都是改判据而不是改被判的代码，判据就是——解析语法树，
只看真正的赋值与调用，不看注释与字符串。

## 隔离靠数据结构，不靠自觉

B 层的全部产出都在 `state["investigation"]` 一个键下。A 层代码不读它，
所以隔离不依赖"每个下游调用点都记得跳过 B 层数据"——
那种要求迟早会失效，BC-50 就是那样失效的。

运行：cd backend && python -m pytest tests/test_investigation_isolation.py -q
"""
import ast
import os
import sys
from typing import List, Set

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service import investigation_layer as inv  # noqa: E402
from service import risk_scorecard as rs  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)
from service.credit_advice import recommend_credit  # noqa: E402
from service.evidence_appendix import render_appendix  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")

#: A 层键。B 层代码写其中任何一个都是越界。
#: `charts` / `knowledge_graph` / `data_points` / `insights` 也在其中——
#: 它们的每个消费者都是在"尽调模式下恒为空"的前提下写的。
PROTECTED_KEYS: Set[str] = {
    "field_checks", "risk_assessment", "completeness", "evidence_store",
    "credit_recommendation", "scoring_view", "search_failures",
    "charts", "knowledge_graph", "data_points", "insights",
    "facts", "references", "final_report",
}


def _parse(relative: str) -> ast.Module:
    with open(os.path.join(APP, relative), encoding="utf-8") as fh:
        return ast.parse(fh.read())


#: 会就地改写容器的方法。`state["charts"].append(...)` 是一次**赋值看不见的
#: 写入**——只查赋值语句的守卫会整条放过它，而那正是最容易顺手写出的越界形式。
MUTATORS = {"append", "extend", "insert", "update", "clear", "remove", "pop", "sort"}

#: 状态字典在本仓库里恒名为 `state`。
STATE_NAME = "state"


def _state_aliases(node: ast.AST) -> Set[str]:
    """`state` 本身，加上函数内直接指向它的别名（`s = state`）。

    只跟一层直接别名。`get_investigation(state)` 返回的是**子字典**，
    不是 state，因此不算别名——那正是 B 层容器该有的样子。
    """
    names = {STATE_NAME}
    for child in ast.walk(node):
        if (isinstance(child, ast.Assign) and isinstance(child.value, ast.Name)
                and child.value.id in names):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _state_key(node: ast.AST, receivers: Set[str]) -> str:
    """`state["k"]` → `"k"`；写到别的容器上、或下标不是字面量，返回空串。

    receiver 必须是 state 本身：`state["investigation"]["charts"]` 的外层
    下标挂在一个 Subscript 上而不是 `state` 上，因此不算 A 层写入——
    B 层容器里有个同名的 charts 子键是合法的，守卫不该被键名迷惑。
    """
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Constant):
        return ""
    if not isinstance(node.slice.value, str):
        return ""
    base = node.value
    return node.slice.value if isinstance(base, ast.Name) and base.id in receivers else ""


def _written_keys(node: ast.AST) -> List[str]:
    """收集本节点内**直接写到 state 上**的键。

    覆盖四种写法：

        state["k"] = v          赋值
        state["k"] += v         增量赋值
        state.setdefault("k")   就地创建
        state["k"].append(...)  **就地改写**——最容易漏掉的一种

    ## 已知边界（写下来，而不是假装没有）

    只跟一层直接别名，且只认字面量键。把 state 传进另一个函数再在那里
    越界，本守卫看不到——那种情况要靠被调函数自己进受检清单。
    一个漏判的守卫比没有守卫更糟：它让人以为查过了。
    """
    receivers = _state_aliases(node)
    keys: List[str] = []
    for child in ast.walk(node):
        targets = []
        if isinstance(child, ast.Assign):
            targets = child.targets
        elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
            targets = [child.target]
        for target in targets:
            key = _state_key(target, receivers)
            if key:
                keys.append(key)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            receiver = child.func.value
            on_state = isinstance(receiver, ast.Name) and receiver.id in receivers
            if (on_state and child.func.attr in ("setdefault", "pop")
                    and child.args and isinstance(child.args[0], ast.Constant)
                    and isinstance(child.args[0].value, str)):
                keys.append(child.args[0].value)
            if child.func.attr in MUTATORS:
                key = _state_key(receiver, receivers)
                if key:
                    keys.append(key)
    return keys


def test_the_guard_catches_in_place_mutation_but_not_the_b_layer_container():
    """守卫的自检。**判据本身必须被判一次**——这个项目已经四次
    被"守卫误伤"咬到（docstring 里的示例、注释里的旧写法、修复说明本身、
    以及 B 层容器里的同名子键）。每次的正确修法都是改判据。

    这里同时钉两面：越界要抓到，合法写入不得误伤。
    """
    sample = ast.parse("\n".join([
        "def f(state):",
        "    box = get_investigation(state)",
        '    state["charts"].append(1)',           # 越界：写到 A 层
        '    alias = state',
        '    alias["insights"] = []',              # 越界：经别名写到 A 层
        '    box["charts"].extend([2])',           # 合法：B 层容器的子键
        '    state["investigation"]["charts"].append(3)',   # 合法：同上
        '    state["raw_sources"] = []',           # 合法：非 A 层键
    ]))
    caught = set(_written_keys(sample))
    assert "charts" in caught, "写到 state 上的就地改写没有被抓到"
    assert "insights" in caught, "经别名的越界没有被抓到"
    assert "raw_sources" in caught, "非保护键也要能被看见，否则守卫是瞎的"
    # 关键的一面：B 层容器里的同名子键**不得**被算成 A 层写入
    assert len([k for k in _written_keys(sample) if k == "charts"]) == 1, \
        "B 层容器的 charts 子键被误判成了 A 层写入"


def _function(module: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"未找到函数 {name}")


# ------------------------------------------------- 一、结构：B 层写不到 A 层

def test_investigation_module_never_writes_an_adjudication_key():
    """调查层模块整体不得写入任何 A 层键。"""
    written = set(_written_keys(_parse("service/investigation_layer.py")))
    trespass = written & PROTECTED_KEYS
    assert not trespass, f"调查层写入了 A 层键：{sorted(trespass)}"


def test_wizard_investigation_path_never_writes_an_adjudication_key():
    """CodeWizard 的 B 层分支同样不得越界。

    它是唯一在编排里构建 B 层的地方，也是最容易顺手写 `state["charts"]`
    的地方——那个字段就在同一个类里被普通研究路径写着。
    """
    module = _parse("service/deep_research_v2/agents/wizard.py")
    for name in ("_build_investigation_layer", "_run_exploratory_pass"):
        written = set(_written_keys(_function(module, name)))
        trespass = written & PROTECTED_KEYS
        assert not trespass, f"{name} 写入了 A 层键：{sorted(trespass)}"


def test_evidence_appendix_module_does_not_know_about_the_investigation_layer():
    """证据附录是追责材料，只放 A 层（隔离要求 3）。

    最强的保证是它**根本不认识** B 层这个概念——
    模块里不出现任何 investigation 标识符，也就没有让它漏进来的路径。
    """
    source = ast.dump(_parse("service/evidence_appendix.py"))
    assert "investigation" not in source.lower(), \
        "证据附录模块出现了调查层标识符，隔离已被打破"


def test_finalize_report_calls_every_canonicalizer():
    """报告的唯一收口入口必须调齐三个区块（BC-50 的纪律）。

    BC-50 的成因是两条路径各自记得调用收口，其中一条漏了一个。
    解法是单一入口；这条断言保证新增区块之后那个入口仍然是齐的。
    """
    module = _parse("service/deep_research_v2/agents/writer.py")
    finalize = _function(module, "_finalize_report")
    called = {
        node.func.attr for node in ast.walk(finalize)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"_ensure_risk_block", "_ensure_investigation_section",
            "_ensure_evidence_appendix"} <= called


def test_investigation_section_is_finalized_before_the_appendix():
    """顺序必须是 A 层正文 → B 层 → 证据附录。

    附录由 `canonicalize_appendix` 另行置底，因此只要 B 层的收口调用
    排在附录之前，三者的相对位置就恒定。
    """
    module = _parse("service/deep_research_v2/agents/writer.py")
    finalize = _function(module, "_finalize_report")
    order = [node.func.attr for node in ast.walk(finalize)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert order.index("_ensure_investigation_section") < order.index("_ensure_evidence_appendix")


# ------------------------------------------------- 二、行为：A 层逐位不变

def _adjudicate(state):
    """跑一遍 A 层，返回全部进入授信决策的数字。"""
    checks = state["field_checks"]
    completeness = compute_completeness(checks)
    assessment = rs.score(state["_company"], checks, completeness)
    credit = recommend_credit(state["_company"], checks, assessment)
    return {
        "level": assessment["level"],
        "composite_score": assessment["composite_score"],
        "gates": tuple(assessment["gates_applied"]),
        "verified_rate": completeness["verified_rate"],
        "required_verified": completeness["required_verified"],
        "amount": credit.get("suggested_amount"),
        "range": (credit.get("range_low"), credit.get("range_high")),
    }


def _dd_state():
    company = find_company("对云岭恒晟精密机械有限公司开展应收账款保理尽职调查")
    assert company is not None, "测试档案 MOCK-002 缺失"
    checks = build_field_checks(scenario="factoring")
    fill_field_checks(company, profile_to_facts(company), checks)
    return {
        "_company": company,
        "company_name": company.get("name") or "",
        "field_checks": checks,
        "completeness": compute_completeness(checks),
        "evidence_store": {},
        "as_of": "2026-08-22",
    }


def test_populating_the_investigation_layer_changes_no_adjudication_number():
    """**计划写下的验收标准**：A 层的等级、额度、核实率逐位不变。

    这是整个阶段 1 的成立条件。B 层写满图表、发现、图谱之后，
    授信侧的每一个数字都必须与 B 层为空时完全相同。
    """
    state = _dd_state()
    before = _adjudicate(state)

    charts, skipped = inv.build_deterministic_charts(state)
    box = inv.get_investigation(state)
    box["charts"].extend(charts)
    for row in skipped:
        inv.record_failure(state, row["field_name"], row["reason"])
    inv.ingest_exploratory_payload(state, {
        "findings": [{"claim": "公司主要客户为三家整车厂，合计占比约六成",
                      "source_result_index": 1}],
        "relations": [{"source": state["company_name"], "target": "某整车厂",
                       "relation": "客户", "source_result_index": 1}],
        "metrics": [{"name": "在手订单", "unit": "万元", "source_result_index": 1,
                     "points": [{"period": "2024年度", "value": 100},
                                {"period": "2025年度", "value": 130}]}],
    }, [{"title": f"{state['company_name']} 2025 年度报告",
         "url": "kb://mock/annual.pdf", "date": "2026-03-20",
         "retrieved_at": "2026-08-22T10:00:00", "summary": "客户结构……"}])

    assert box["charts"], "前提：B 层确实产出了内容，否则这条断言是空的"
    assert box["findings"], "前提：B 层确实接纳了发现"
    assert _adjudicate(state) == before, "B 层影响了 A 层的授信数字"


def test_deterministic_charts_read_only_verified_fields():
    """确定性图表只画已核实字段。

    未核实的取值画成图，那张图就成了绕过闸门的旁路——
    读者不会去核对折线下面写的是"未核实"。
    """
    state = _dd_state()
    for check in state["field_checks"]:
        if check.get("field_id") == "revenue":
            check["status"] = "unverified"
    charts, skipped = inv.build_deterministic_charts(state)
    titles = [c["plain_title"] for c in charts]
    assert "营业收入趋势" not in titles
    assert any(row["field_id"] == "revenue" for row in skipped), "拒绝要留痕"


def test_chart_numbers_match_the_verified_value_verbatim():
    """图上的数与证据附录引用的取值串必须逐位一致。

    一旦允许换算，同一个数就有了两个口径——BC-31 / BC-52 的形态。
    """
    state = _dd_state()
    charts, _ = inv.build_deterministic_charts(state)
    revenue = next(c for c in charts if c["provenance"].get("field_id") == "revenue")
    source_value = revenue["provenance"]["source_value"]
    for point in revenue["series"]:
        assert point["text"] in source_value, \
            f"图上的 {point['text']} 不是取值串里的原文写法"


# ------------------------------------------------- 三、行为：附录不含 B 层

def test_evidence_appendix_contains_no_investigation_content():
    """证据附录里不得出现任何 B 层痕迹（隔离要求 3）。"""
    state = _dd_state()
    charts, _ = inv.build_deterministic_charts(state)
    box = inv.get_investigation(state)
    box["charts"].extend(charts)
    box["findings"].append({
        "claim": "一条只应出现在调查层的说法",
        "source": {"title": "某报告", "published_at": "2026-01-01",
                   "retrieved_at": "2026-08-22T10:00:00"},
        "verified": False,
    })

    appendix = render_appendix(
        state["field_checks"], state["evidence_store"], state["completeness"],
        search_failures=[], as_of=state["as_of"], section_failures=[])

    assert inv.EXPLORATORY_BADGE not in appendix
    assert inv.SECTION_MARKER not in appendix
    assert "一条只应出现在调查层的说法" not in appendix


def test_report_order_is_body_then_investigation_then_appendix():
    """终稿三段的先后必须固定，且 B 层夹在正文与附录之间。

    附录是追责材料，永远置底；B 层若跑到附录之后，读者会把它当成附录的
    一部分——那正是隔离要求 1 要防的"混排"。
    """
    from service.evidence_appendix import canonicalize_appendix, render_appendix as _ra

    state = _dd_state()
    charts, _ = inv.build_deterministic_charts(state)
    inv.get_investigation(state)["charts"].extend(charts)

    body = "# 尽职调查报告\n\n## 结论\n\n正文内容。"
    with_b = inv.canonicalize_investigation_section(
        body, inv.render_investigation_section(state))
    final = canonicalize_appendix(with_b, _ra(
        state["field_checks"], state["evidence_store"], state["completeness"],
        search_failures=[], as_of=state["as_of"], section_failures=[]))

    from service.evidence_appendix import APPENDIX_MARKER
    assert final.index("正文内容") < final.index(inv.SECTION_MARKER) < final.index(APPENDIX_MARKER)


def test_reports_without_an_investigation_layer_are_left_untouched():
    """普通研究流程没有 B 层，收口不得动正文一个字。

    收敛函数会顺手 strip 正文；那点空白无害，但它会把"本次没有 B 层"
    记成一次改动，于是日志里多出一条恒亮的告警——
    **一个恒亮的告警等于没有告警**，真正的问题会被它淹没。
    """
    from service.deep_research_v2.agents.writer import LeadWriter

    writer = LeadWriter(llm_api_key="x", llm_base_url="http://127.0.0.1:1/v1")
    body = "# 普通研究报告" + "\n\n" + "正文内容。" + "\n\n"   # 刻意留尾部空白
    state = {"final_report": body, "investigation": inv.empty_investigation()}
    assert writer._ensure_investigation_section(state) is False
    assert state["final_report"] == body, "无 B 层时正文被改动了"


def test_a_report_that_has_the_section_is_still_canonicalized():
    """反面：正文里有 B 层内容时，收口照常工作。

    与上一条互为反面——只钉"不动"很容易写出一个什么都不做的收口。
    """
    from service.deep_research_v2.agents.writer import LeadWriter

    writer = LeadWriter(llm_api_key="x", llm_base_url="http://127.0.0.1:1/v1")
    state = _dd_state()
    charts, _ = inv.build_deterministic_charts(state)
    inv.get_investigation(state)["charts"].extend(charts)
    state["final_report"] = "# 尽职调查报告" + "\n\n" + "正文内容。"

    assert writer._ensure_investigation_section(state) is True
    assert inv.SECTION_MARKER in state["final_report"]
    assert "正文内容" in state["final_report"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
