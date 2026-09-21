"""Protocol regression tests; these do not measure a live model's error recall."""
import copy
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.analysis_quality import CHECKS, enforce_analysis_review
from service.deep_research_v2.agents.critic import CriticMaster
from service.review_verdict import derive_verdict, unresolved_blocking_issues


def receipt():
    return {"overall_assessment": {"quality_score": 9, "verdict": "pass"}, "issues": [],
            "analysis_checks": [{"id": key, "status": "supported", "reason": "已对照来源与口径"}
                                for key in CHECKS]}


class CriticStub(CriticMaster):
    def __init__(self):
        self.logger = logging.getLogger("analysis-test")


def test_missing_review_blocks_agent_dd_but_not_legacy():
    raw = {"overall_assessment": {"quality_score": 9, "verdict": "pass"}, "issues": []}
    state = {"research_strategy": "agent", "due_diligence_mode": True, "final_report": "报告"}
    result = CriticStub().merge_review(state, raw)
    assert result["degraded"]
    assert result["overall_assessment"]["verdict"] == "major_issues"
    assert len(result["issues"]) == 4
    assert raw["issues"] == []
    state["research_strategy"] = "classic"
    assert CriticStub().merge_review(state, copy.deepcopy(raw))["overall_assessment"]["verdict"] == "pass"


@pytest.mark.parametrize("key,quote,reason", [
    ("cashflow_attribution", "应收增加240占现金流同比下降300的80%，因此是主因。",
     "本期调节额240并非两期调节额之差，缺少上期调节表，不能解释同比300。"),
    ("receipt_reconciliation", "期末应收已收回90。",
     "长账龄90加短账龄40=期末应收回款130，另有预收70，总额200；不能把90代替130。"),
])
def test_identified_errors_cannot_be_passed_or_lost_at_iteration_limit(key, quote, reason):
    raw = receipt()
    row = next(c for c in raw["analysis_checks"] if c["id"] == key)
    row.update(status="issue", reason=reason, report_quote=quote)
    result = enforce_analysis_review(raw, quote)
    assert raw["issues"] == []
    assert result["issues"][0]["issue_type"] == "analysis_quality_error"
    result["issues"][0]["severity"] = "minor"
    assert derive_verdict(result["issues"], 9, "pass")["verdict"] == "major_issues"
    assert unresolved_blocking_issues(result["issues"])


@pytest.mark.parametrize("mutation", ["absent", "duplicate", "bad_status", "empty_reason", "invented_quote"])
def test_malformed_receipts_are_incomplete_review_not_financial_error(mutation):
    raw = receipt()
    rows = raw["analysis_checks"]
    if mutation == "absent":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0].copy())
    elif mutation == "bad_status":
        rows[0]["status"] = "pass"
    elif mutation == "empty_reason":
        rows[0]["reason"] = " "
    else:
        rows[0].update(status="issue", report_quote="不存在的片段")
    result = enforce_analysis_review(raw, "实际报告")
    assert result["degraded"]
    assert all(i["issue_type"] == "review_not_executed" for i in result["issues"])


def test_cautious_correct_report_and_reasoned_not_applicable_can_pass():
    raw = receipt()
    raw["analysis_checks"][2]["reason"] = "仅有本期调节表，报告明确不能完成同比归因，没有声称贡献率。"
    raw["analysis_checks"][3].update(status="not_applicable", reason="问题和报告只涉及工商主体识别，不涉及回款。")
    result = enforce_analysis_review(raw, "报告")
    assert not result["issues"]
    assert derive_verdict(result["issues"], 9, "pass")["verdict"] == "pass"
