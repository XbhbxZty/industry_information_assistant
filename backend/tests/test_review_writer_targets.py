"""Actual report anchors grant bounded AI repair, never authority over evidence."""
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.analysis_quality import CHECKS, report_quote_spans
from service.deep_research_v2.agents.critic import CriticMaster
from service.deep_research_v2.agents.writer import LeadWriter
from service.deep_research_v2.investigation_report import START, END, append_investigation_report, plain
from service.deep_research_v2.review_targets import analysis_revision_issues


def state():
    citation = {"source_id": "s1", "quote_id": "q1", "title": "原报表", "quote": "公司本期经营收款100万元。"}
    notebook = {
        "status": "stalled", "summary": "该企业的 *唯一主因* 是 [回款]，因此不存在风险 <待查>。",
        "findings": [{"kind": "analysis", "claim": "公司回款情况仍需逐笔核验。", "verified": False,
                      "citations": [citation]}],
        "missing_materials": ["请补充银行流水用于核对债权。"],
        "investigation_plan": [{"id": "p1", "question": "收款是否已完成勾稽？", "done_when": "分清债权并说明限制",
                                "status": "answered", "answer": "目前只能确认原材料记载了收款100万元。",
                                "limitations": "尚未完成独立核查。", "calculation_required": False,
                                "citations": [citation], "calculation_ids": ["c1"]}],
        "calculations": [{"id": "c1", "label": "收款合计", "expression": "amount + 0", "result": "100",
                          "result_unit": "万元", "limitations": "计算不是独立核实。",
                          "variables": {"amount": {"value": "100", "unit": "万元", "period": "本期",
                                                   "subject": "公司", **citation}}}],
        "sources": {"s1": {"read": True, "read_texts": [citation["quote"]], "quote_options": {"q1": citation["quote"]}}},
        "actions": [{"action": "calculate"}],
    }
    return {"query": "解释回款情况", "research_strategy": "agent", "due_diligence_mode": True,
            "phase": "reviewing", "iteration": 0, "max_iterations": 3, "field_checks": [],
            "risk_assessment": {"level": "资料不足", "requires_human_review": True},
            "errors": [], "critic_feedback": [], "facts": [], "data_points": [], "outline": [],
            "agent_investigation": notebook,
            "final_report": append_investigation_report("# 尽调报告\n\n规则评级：资料不足，不得放款。", notebook)}


def issue(data, quote, *, anchored=True):
    result = {"issue_type": "hallucination", "severity": "major", "resolved": False,
              "description": "该判断没有足够证据", "suggestion": "撤回无依据结论并保留限制", "evidence": quote}
    if anchored:
        result["report_quote_id"] = next(key for key, row in report_quote_spans(data["final_report"]).items()
                                         if row["quote"] == quote)
    return result


def revised(data):
    return {"summary": "现有材料只能说明收款金额，尚不能据此识别主因或排除风险。",
            "findings": [{"index": 0, "claim": "公司回款情况仍需逐笔核验，不能据此排除风险。"}],
            "missing_materials": ["请补充银行流水用于核对债权。"],
            "investigation_plan": [{k: v for k, v in data["agent_investigation"]["investigation_plan"][0].items()
                                    if k in ("id", "status", "answer", "limitations")}]}


class WriterStub(LeadWriter):
    def __init__(self, revision):
        self.name = "target-writer"
        self.logger = logging.getLogger(self.name)
        self.call_llm = AsyncMock(return_value=json.dumps(revision, ensure_ascii=False))

    def add_message(self, *_):
        pass


@pytest.mark.parametrize("field,quote", [
    ("summary", plain("该企业的 *唯一主因* 是 [回款]，因此不存在风险 <待查>。")),
    ("findings[0].claim", "- [analysis] 公司回款情况仍需逐笔核验。"),
    ("investigation_plan[0].answer", "- 当前答复：目前只能确认原材料记载了收款100万元。"),
    ("investigation_plan[0].limitations", "- 限制与缺口：尚未完成独立核查。"),
    ("missing_materials[0]", "- 请补充银行流水用于核对债权。"),
])
@pytest.mark.parametrize("anchored", [True, False])
def test_only_actual_editable_lines_are_returned_without_mutating_input(field, quote, anchored):
    data = state()
    data["critic_feedback"] = [issue(data, quote, anchored=anchored)]
    before = copy.deepcopy(data)
    selected = analysis_revision_issues(data)
    assert len(selected) == 1 and selected[0]["repair_target"]["field"] == field
    target = selected[0]["repair_target"]
    assert data["final_report"][target["start"]:target["end"]] == quote
    assert data == before
    selected[0]["resolved"] = True
    assert data == before


@pytest.mark.parametrize("quote", [
    "规则评级：资料不足，不得放款。",
    "以下为 AI 基于材料提出的分析与待核实线索，不替代上文规则评级。引文存在不等于分析结论已获独立核实。",
    "调查状态：stalled", "- 完成条件：分清债权并说明限制",
    "- p1 [已回答，待复核] 收款是否已完成勾稽？",
    "- 依据：公司本期经营收款100万元。", "- 来源：原报表；s1/q1",
    "- 计算底稿：c1", "- c1 收款合计：amount + 0 = 100 万元",
    "- amount=100 万元；期间：本期；主体：公司；s1/q1", "- 限制：计算不是独立核实。",
])
@pytest.mark.parametrize("anchored", [True, False])
def test_protected_and_frozen_report_lines_cannot_authorize_ai_revision(quote, anchored):
    data = state()
    # Source-reference lines intentionally have no offered report quote ID.
    anchors = {row["quote"] for row in report_quote_spans(data["final_report"]).values()}
    data["critic_feedback"] = [issue(data, quote, anchored=anchored and quote in anchors)]
    assert analysis_revision_issues(data) == []
    writer = WriterStub(revised(data))
    asyncio.run(writer._revise_agent_analysis(data))
    writer.call_llm.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["old_anchor", "conflicting_quote", "unescaped",
                                       "duplicate_line", "duplicate_appendix", "resolved", "protocol", "unknown_type"])
def test_stale_missing_or_ambiguous_locations_are_not_editing_authority(mutation):
    data = state()
    quote = plain(data["agent_investigation"]["summary"])
    row = issue(data, quote, anchored=mutation not in ("unescaped", "duplicate_line"))
    if mutation == "old_anchor":
        data["final_report"] = "新报告版本\n" + data["final_report"]
    elif mutation == "conflicting_quote":
        row["evidence"] = "修改过的引文"
    elif mutation == "unescaped":
        row["evidence"] = data["agent_investigation"]["summary"]
    elif mutation == "duplicate_line":
        data["final_report"] = quote + "\n\n" + data["final_report"]
    elif mutation == "duplicate_appendix":
        data["final_report"] += "\n" + START + "\n" + END
    elif mutation == "resolved":
        row["resolved"] = True
    elif mutation == "protocol":
        row["issue_type"] = "review_not_executed"
    else:
        row["issue_type"] = "new_model_invented_type"
    data["critic_feedback"] = [row]
    assert analysis_revision_issues(data) == []


def test_unique_exact_short_quote_selects_its_complete_editable_report_line():
    data = state()
    data["critic_feedback"] = [issue(data, "因此不存在风险", anchored=False)]
    selected = analysis_revision_issues(data)
    assert len(selected) == 1
    target = selected[0]["repair_target"]
    assert target["field"] == "summary"
    assert target["report_quote"] == plain(data["agent_investigation"]["summary"])
    assert data["final_report"][target["start"]:target["end"]] == target["report_quote"]


@pytest.mark.parametrize("mutation", ["other_editable", "protected_body", "overlapping", "cross_line",
                                       "source_only", "unescaped_fragment"])
def test_short_quotes_cannot_cross_boundaries_or_bypass_exact_unique_location(mutation):
    data = state()
    quote = "因此不存在风险"
    if mutation == "other_editable":
        data["agent_investigation"]["findings"][0]["claim"] = "该公司因此不存在风险"
        data["final_report"] = append_investigation_report("规则正文", data["agent_investigation"])
    elif mutation == "protected_body":
        data["final_report"] = quote + "\n\n" + data["final_report"]
    elif mutation == "overlapping":
        data["agent_investigation"]["summary"] = "该样例结论为AAA。"
        data["final_report"] = append_investigation_report("规则正文", data["agent_investigation"])
        quote = "AA"
    elif mutation == "cross_line":
        quote = plain(data["agent_investigation"]["summary"]) + "\n\n### 待解决问题与补件"
    elif mutation == "source_only":
        quote = "公司本期经营收款100万元。"
    else:
        quote = "*唯一主因*"
    data["critic_feedback"] = [issue(data, quote, anchored=False)]
    assert analysis_revision_issues(data) == []


def test_anchored_repeated_answers_are_separate_fields_but_unanchored_text_is_ambiguous():
    data = state()
    other = copy.deepcopy(data["agent_investigation"]["investigation_plan"][0])
    other.update(id="p2", question="另一个问题是否完成？")
    data["agent_investigation"]["investigation_plan"].append(other)
    data["final_report"] = append_investigation_report("规则正文", data["agent_investigation"])
    quote = "- 当前答复：" + other["answer"]
    anchors = [key for key, row in report_quote_spans(data["final_report"]).items() if row["quote"] == quote]
    data["critic_feedback"] = [dict(issue(data, quote), report_quote_id=key) for key in anchors]
    assert [row["repair_target"]["field"] for row in analysis_revision_issues(data)] == [
        "investigation_plan[0].answer", "investigation_plan[1].answer"]
    data["critic_feedback"] = [issue(data, quote, anchored=False)]
    assert analysis_revision_issues(data) == []


def test_identical_finding_lines_are_not_guessed_as_one_editable_field():
    data = state()
    data["agent_investigation"]["findings"].append(copy.deepcopy(data["agent_investigation"]["findings"][0]))
    data["final_report"] = append_investigation_report("规则正文", data["agent_investigation"])
    data["critic_feedback"] = [issue(data, "- [analysis] 公司回款情况仍需逐笔核验。")]
    assert analysis_revision_issues(data) == []


def test_notebook_not_matching_report_cannot_manufacture_a_target():
    data = state()
    data["critic_feedback"] = [issue(data, plain(data["agent_investigation"]["summary"]))]
    data["agent_investigation"]["summary"] = "未在当前报告中交付的新结论"
    assert analysis_revision_issues(data) == []


def test_unanchored_whole_indented_line_retains_actual_report_target():
    data = state()
    data["critic_feedback"] = [issue(data, "  - 限制与缺口：尚未完成独立核查。", anchored=False)]
    assert analysis_revision_issues(data)[0]["repair_target"]["field"] == "investigation_plan[0].limitations"


def test_fallback_questions_are_not_mislabelled_as_existing_missing_material_fields():
    data = state()
    data["agent_investigation"]["questions"] = data["agent_investigation"].pop("missing_materials")
    data["final_report"] = append_investigation_report("规则正文", data["agent_investigation"])
    data["critic_feedback"] = [issue(data, "- 请补充银行流水用于核对债权。")]
    assert analysis_revision_issues(data) == []


def test_legacy_analysis_issues_remain_compatible_and_selected_issues_are_not_silently_cut_to_eight():
    data = state()
    data["critic_feedback"] = [{"id": f"issue{i}", "issue_type": "analysis_quality_error", "resolved": False,
                                "description": f"问题{i}"} for i in range(10)]
    writer = WriterStub(revised(data))
    asyncio.run(writer._revise_agent_analysis(data))
    prompt = writer.call_llm.call_args.kwargs["user_prompt"].split("\n返回JSON：", 1)[0]
    assert len(json.loads(prompt)["issues"]) == 10


@pytest.mark.parametrize("use_anchor", [True, False])
def test_critic_ordinary_issue_reaches_writer_without_closing_issue_or_changing_frozen_state(use_anchor):
    data = state()
    summary = plain(data["agent_investigation"]["summary"])
    anchor = issue(data, summary)["report_quote_id"]
    answer = "- 当前答复：" + data["agent_investigation"]["investigation_plan"][0]["answer"]
    raw = {"score": 5, "summary": "自主分析存在无依据结论",
           "issues": [{"type": "hallucination", "severity": "major",
                       **({"report_quote_id": anchor} if use_anchor else {"quote": "因此不存在风险"}),
                       "reason": "不能从收款金额排除全部风险", "fix": "撤回无依据断言并披露限制"}],
           "checks": [{"id": key, "status": "not_applicable", "reason": "本例单测不含该项量化判断", "report_quote": ""}
                      for key in CHECKS],
           "question_checks": [{"id": "p1", "status": "supported", "reason": "答复如实限定材料记载范围",
                                "report_quote": answer, "needs_more_evidence": False, "followup_question": ""}]}

    class CriticStub(CriticMaster):
        def __init__(self):
            self.name = "target-critic"
            self.logger = logging.getLogger(self.name)
            self.call_llm = AsyncMock(return_value=(json.dumps(raw, ensure_ascii=False), {"finish_reason": "stop"}))

        def add_message(self, *_):
            pass

    asyncio.run(CriticStub().process(data))
    assert data["phase"] == "revising"
    before = copy.deepcopy(data)
    writer = WriterStub(revised(data))
    asyncio.run(writer._revise_agent_analysis(data))
    writer.call_llm.assert_awaited_once()
    assert data["agent_investigation"]["summary"] != before["agent_investigation"]["summary"]
    for key in ("risk_assessment", "field_checks", "critic_feedback"):
        assert data[key] == before[key]
    for key in ("status", "calculations", "actions", "sources"):
        assert data["agent_investigation"][key] == before["agent_investigation"][key]
    assert all(not row["resolved"] for row in data["critic_feedback"])
    prompt = json.loads(writer.call_llm.call_args.kwargs["user_prompt"].split("\n返回JSON：", 1)[0])
    assert prompt["report_targets"][0]["field"] == "summary"
    assert prompt["report_targets"][0]["report_quote"] == summary


def test_invalid_ordinary_issue_revision_preserves_notebook_atomically():
    data = state()
    data["critic_feedback"] = [issue(data, plain(data["agent_investigation"]["summary"]))]
    update = revised(data)
    update["findings"][0]["claim"] = "短"
    before = copy.deepcopy(data["agent_investigation"])
    writer = WriterStub(update)
    asyncio.run(writer._revise_agent_analysis(data))
    assert data["agent_investigation"] == before
    assert "修订发现长度无效" in data["errors"][-1]
