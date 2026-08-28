# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
修订轮不得让模型改写尽调正文（BC-71）

## 这一轮在钉什么

`_write_report` 立过一条契约：「LLM 保留候选抽取职责，**不拥有在最终报告
新增事实、来源或结论的权限**」。修订这条路绕过了它——`_revise_report`
把报告截断到 6000 字喂给模型，用模型输出**整份替换**，
而 `_finalize_report` 只找回三个带锚点的区块。

2026-08-22 真实运行实测（会话 dd-1787413212824）：

    复核人看到    7284 字，17 行逐项核查，有结论段
    落盘终稿      5147 字，**0 行逐项核查，无结论段**

消失的包括报告标题、主体名称、研究截止日，以及那句
「未核实既不表示存在风险……不得据此放款」——系统的核心免责语义。

## 最强的断言形式是"模型根本没被问过"

只断言"正文还在"是不够的：模型也可能恰好没删。因此这里把 `call_llm`
换成一个会抛错的桩——**它一旦被调用，测试就失败**。
契约是"模型无权改写"，那就直接钉住"模型没有被咨询"。

运行：cd backend && python -m pytest tests/test_revision_preserves_report.py -q
"""
import ast
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service import risk_scorecard as rs  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)
from service.credit_advice import recommend_credit  # noqa: E402
from service.deep_research_v2.agents.writer import LeadWriter  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")

#: 确定性正文的指纹：代码渲染器逐项输出的固定句式
FIELD_ROW = "；取证时间："

#: 这句话消失过一次。它是整个系统的核心免责语义，必须能被单独断言。
DISCLAIMER = "既不表示存在风险"


def _writer() -> LeadWriter:
    #  base_url 指向一个不可达端口：万一真去调模型，会失败而不是静默成功
    return LeadWriter(llm_api_key="x", llm_base_url="http://127.0.0.1:1/v1")


def _dd_state():
    company = find_company("请对东莞市泰锐精密传动件有限公司做贷前尽职调查，授信2000万元")
    assert company is not None, "测试档案 MOCK-001 缺失"
    checks = build_field_checks()
    fill_field_checks(company, profile_to_facts(company), checks)
    completeness = compute_completeness(checks)
    assessment = rs.score(company, checks, completeness)
    assessment["credit_recommendation"] = recommend_credit(company, checks, assessment)
    return {
        "due_diligence_mode": True,
        "phase": ResearchPhase.REVISING.value,
        "query": "贷前尽职调查",
        "company_name": company.get("name"),
        "field_checks": checks,
        "completeness": completeness,
        "risk_assessment": assessment,
        "evidence_store": {},
        "as_of": "2026-08-22",
        "outline": [],
        "messages": [],
        "references": [],
        "critic_feedback": [],
        "errors": [],
        # 普通研究路径的修订会读 facts；两条路径共用这个夹具，
        # 缺了它反面用例会因 KeyError 而不是因"调了模型"失败——
        # 那样这条断言就变成了对拼写错误的测试
        "facts": [],
    }


def _rendered(state) -> str:
    writer = _writer()
    writer._write_structured_due_diligence_report(state)
    return state["final_report"]


def _explode(*_args, **_kwargs):
    raise AssertionError("尽调模式下修订轮不得调用模型")


# ------------------------------------------------- 一、模型根本没被问过

def test_revision_never_consults_the_model_in_due_diligence_mode():
    """契约是「模型无权改写」，那就直接钉「模型没有被咨询」。

    只断言"正文还在"不够——模型也可能恰好没删。
    """
    state = _dd_state()
    _rendered(state)
    writer = _writer()
    writer.call_llm = _explode                    # 一旦被调用，测试即失败

    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))            # 走 process 入口，覆盖真实路径

    assert state["phase"] == ResearchPhase.REVIEWING.value


def test_non_due_diligence_revision_still_goes_through_the_model():
    """反面：普通研究流程的修订仍然是模型的活。

    只钉"尽调不调模型"，很容易写出一个把所有修订都关掉的实现。
    """
    state = _dd_state()
    _rendered(state)
    state["due_diligence_mode"] = False
    state["phase"] = ResearchPhase.REVISING.value
    state["critic_feedback"] = [{"id": "i1", "severity": "major",
                                 "description": "某处需要补充", "suggestion": "补充"}]
    writer = _writer()
    writer.call_llm = _explode

    with pytest.raises(AssertionError, match="不得调用模型"):
        asyncio.run(writer.process(state))


# ------------------------------------------------- 二、正文必须被恢复

def test_a_mangled_report_is_restored_to_the_deterministic_rendering():
    """本条的直接回归：模型吃掉正文之后，修订轮必须把它整份还回来。"""
    state = _dd_state()
    original = _rendered(state)
    rows_before = original.count(FIELD_ROW)
    assert rows_before >= 10, "前提：确定性正文本来有逐项核查行"

    # 模拟实测现象：只剩被收口的区块，标题/结论/逐项正文全没了
    from service.investigation_layer import SECTION_MARKER  # noqa: WPS433
    tail_at = original.find(SECTION_MARKER)
    state["final_report"] = original[tail_at:] if tail_at > 0 else "只剩一句话。"
    assert state["final_report"].count(FIELD_ROW) == 0

    writer = _writer()
    writer.call_llm = _explode
    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))

    restored = state["final_report"]
    assert restored.count(FIELD_ROW) == rows_before, "逐项核查行没有被还回来"
    assert "## 结论" in restored
    assert DISCLAIMER in restored, "核心免责语义仍然缺失"
    assert state["company_name"] in restored, "报告标题里的主体名没有还回来"


def test_rerendering_is_idempotent():
    """重跑渲染必须逐字相同——它是清单/证据/评分的纯函数。

    不相同就说明渲染里混进了随机或时间量，那样"重跑一次恢复原状"
    这个修法本身就不成立。
    """
    state = _dd_state()
    first = _rendered(state)
    writer = _writer()
    writer.call_llm = _explode
    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))
    assert state["final_report"] == first


def test_report_draft_is_re_emitted_so_the_ui_matches_the_final():
    """修订后要重发 report_draft，否则界面停在旧版。

    BC-71 的第二层后果正是这个：复核人签的与交付的不是同一份。
    """
    state = _dd_state()
    _rendered(state)
    state["messages"] = []
    writer = _writer()
    writer.call_llm = _explode
    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))

    kinds = [m.get("type") for m in state["messages"]]
    assert "report_draft" in kinds, "修订后没有重发报告，界面会停在旧版"


# ------------------------------------------------- 三、无法处理的意见要留痕

def test_unaddressable_feedback_is_recorded_not_silently_burned():
    """评审意见在结构上无法处理时，必须写明原因。

    此前这几轮迭代静默烧掉，最后只留一句"达到最大迭代轮次"——
    读者无从知道评审说了什么、以及为什么一条都没被处理（BC-51 同一纪律）。
    """
    state = _dd_state()
    _rendered(state)
    state["critic_feedback"] = [
        {"id": "i1", "severity": "critical", "description": "把未核实项写成了事实"},
        {"id": "i2", "severity": "major", "description": "缺少行业对比"},
    ]
    writer = _writer()
    writer.call_llm = _explode
    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))

    joined = "\n".join(state["errors"])
    assert "2 条意见" in joined
    assert "无权改写" in joined
    assert "把未核实项写成了事实" in joined, "要留下评审到底说了什么"


def test_no_feedback_produces_no_noise():
    """没有未决意见时不要留痕——一个恒亮的告警等于没有告警。"""
    state = _dd_state()
    _rendered(state)
    state["critic_feedback"] = []
    writer = _writer()
    writer.call_llm = _explode
    state["phase"] = ResearchPhase.REVISING.value
    asyncio.run(writer.process(state))
    assert state["errors"] == []


# ------------------------------------------------- 四、结构断言

def _function(module: ast.Module, name: str):
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"未找到函数 {name}")


def test_the_due_diligence_branch_returns_before_any_model_call():
    """结构上保证尽调分支在任何模型调用之前就返回。

    走 AST 而不是字符串匹配：这个项目已五次被"守卫误伤"咬到，
    字符串守卫会命中注释、docstring 与修复说明本身。
    """
    with open(os.path.join(APP, "service/deep_research_v2/agents/writer.py"),
              encoding="utf-8") as fh:
        module = ast.parse(fh.read())
    revise = _function(module, "_revise_report")

    body = [n for n in revise.body if not (isinstance(n, ast.Expr)
                                           and isinstance(n.value, ast.Constant))]
    first = body[0]
    assert isinstance(first, ast.If), "尽调分支必须是函数的第一条语句"
    test_src = ast.dump(first.test)
    assert "due_diligence_mode" in test_src, "第一条判断不是尽调模式"
    assert any(isinstance(n, ast.Return) for n in ast.walk(first)), \
        "尽调分支必须直接返回"
    assert not any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "call_llm"
        for n in ast.walk(first)
    ), "尽调分支里不得出现模型调用"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
