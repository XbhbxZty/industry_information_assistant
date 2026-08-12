"""
Critic 扫描器门控与降级路径测试

对应外部评审的 P0 两条：

④ 扫描器 critical 未门控裁决
   此前扫描结论只被追加进 issues，不影响 verdict。LLM 返回 pass 时
   流程照样把状态置为 COMPLETED——扫描器抓到了违规，报告仍然放行。
   **实现了机制，但机制不约束结果，等于只写了一条日志。**

⑤ LLM 失败路径未完整覆盖
   process() 未捕获 API 异常（异常冒泡则 merge_review 不执行，
   确定性结论一并丢失）；解析失败返回空字典时不触发 degraded。

这类缺陷不会让任何数字变难看——报告照常产出、分数照常显示，
只有断言能把它们钉住。

运行：cd backend && python tests/test_critic_gate.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.critic import CriticMaster  # noqa: E402
from service.claim_scanner import scan_report  # noqa: E402

_gate = CriticMaster._enforce_scanner_gate


def _scan(severity, n=1):
    return [{"severity": severity, "target_section": f"field_{i}",
             "issue_type": "unverified_as_fact"} for i in range(n)]


def _result(verdict="pass", score=9.0):
    return {"overall_assessment": {"verdict": verdict, "quality_score": score},
            "issues": []}


# ---------- ④ 扫描器门控 ----------

def test_critical_必须否决_pass():
    r = _gate(_result("pass", 9.0), _scan("critical"))
    assert r["overall_assessment"]["verdict"] != "pass", "扫描器 critical 不得放行"
    assert r["overall_assessment"]["verdict"] == "major_issues"


def test_critical_必须压低质量分():
    r = _gate(_result("pass", 9.0), _scan("critical"))
    assert r["overall_assessment"]["quality_score"] <= 3.0, \
        "critical 时分数上限 3，否则下游按分数放行"


def test_major_必须否决_pass():
    r = _gate(_result("pass", 8.0), _scan("major"))
    assert r["overall_assessment"]["verdict"] != "pass"
    assert r["overall_assessment"]["quality_score"] <= 6.0


def test_无扫描问题时不干预裁决():
    r = _gate(_result("pass", 9.0), [])
    assert r["overall_assessment"]["verdict"] == "pass", "无扫描问题时不应改动"
    assert r["overall_assessment"]["quality_score"] == 9.0


def test_不得抬高已有的低分():
    """门控只压不抬——LLM 判 2 分时不能因为 cap=3 反而变成 3"""
    r = _gate(_result("major_issues", 2.0), _scan("critical"))
    assert r["overall_assessment"]["quality_score"] == 2.0


def test_门控留痕可审计():
    r = _gate(_result("pass", 9.0), _scan("critical", 2))
    g = r["overall_assessment"].get("scanner_gate_applied")
    assert g, "门控必须留痕，否则无法解释裁决为何被推翻"
    assert g["critical"] == 2
    assert g["original_verdict"] == "pass"
    assert g["original_score"] == 9.0


def test_缺少_overall_assessment_也能门控():
    """LLM 返回结构不全时，门控仍须生效而非静默跳过"""
    r = _gate({"issues": []}, _scan("critical"))
    assert r["overall_assessment"]["verdict"] == "major_issues"


def test_分数非数值时按上限处理():
    r = _gate(_result("pass", "很好"), _scan("critical"))
    assert r["overall_assessment"]["quality_score"] == 3.0


# ---------- ⑤ 降级路径 ----------

class _Stub(CriticMaster):
    """只借用 merge_review，不做真实 LLM 调用"""
    def __init__(self):
        import logging
        self.logger = logging.getLogger("stub")


def _state(checks=None, text=""):
    return {"field_checks": checks or [], "final_report": text,
            "draft_sections": {}, "errors": []}


def test_llm_返回None时标记降级():
    r = _Stub().merge_review(_state(), None)
    assert r["degraded"] is True, "LLM 不可用必须显式标记，不得静默"
    assert r["overall_assessment"]["verdict"] != "pass"


def test_llm_返回空字典时同样标记降级():
    """此前只判 isinstance(dict)，{} 会被当成有效结果"""
    r = _Stub().merge_review(_state(), {})
    assert r.get("degraded") is True, "空字典是解析失败，不是有效审核结果"


def test_llm_返回缺关键字段时标记降级():
    r = _Stub().merge_review(_state(), {"foo": "bar"})
    assert r.get("degraded") is True


def test_llm_返回有效结构时不标记降级():
    r = _Stub().merge_review(_state(), _result("pass", 8.0))
    assert not r.get("degraded")
    assert r["overall_assessment"]["verdict"] == "pass"


def test_降级时扫描结论仍然保留():
    """LLM 挂掉不应导致确定性检出丢失——这是修复的核心"""
    checks = [{
        "field_id": "guarantee", "field_name": "对外担保", "category": "relation",
        "section_id": "sec_6", "required": True, "status": "unverified",
        "value": None, "sources": [], "attempted_sources": [],
        "failure_reason": "数据源不覆盖", "conflict_detail": [], "checked_at": "",
    }]
    text = "## 关联关系与对外担保\n经核查，该公司不存在对外担保事项。"
    r = _Stub().merge_review(_state(checks, text), None)
    assert r["degraded"] is True
    assert len(r["issues"]) > 0, "LLM 失败时扫描器结论必须保留"
    assert r["overall_assessment"]["verdict"] != "pass"


def test_降级且有扫描critical时走门控():
    checks = [{
        "field_id": "litigation", "field_name": "涉诉记录", "category": "judicial",
        "section_id": "sec_5", "required": True, "status": "unverified",
        "value": None, "sources": [], "attempted_sources": [],
        "failure_reason": "司法源超时", "conflict_detail": [], "checked_at": "",
    }]
    text = "## 司法与合规风险\n经核查，该公司无任何涉诉记录。"
    r = _Stub().merge_review(_state(checks, text), None)
    if any(i.get("severity") == "critical" for i in r["issues"]):
        assert r["overall_assessment"]["quality_score"] <= 3.0


# ---------- 审核文本同源 + 真消融 ----------

def test_扫描器与LLM统一优先审核最终报告():
    state = _state(text="最终报告中的新断言")
    state["draft_sections"] = {"sec_1": "整合前草稿"}
    state["outline"] = [{"id": "sec_1", "title": "基本情况"}]
    assert _Stub()._content_for_review(state) == "最终报告中的新断言"


def test_最终报告为空时才回退章节草稿():
    state = _state(text="")
    state["draft_sections"] = {"sec_1": "整合前草稿"}
    state["outline"] = [{"id": "sec_1", "title": "基本情况"}]
    text = _Stub()._content_for_review(state)
    assert "基本情况" in text and "整合前草稿" in text


def test_扫描器消融在合并阶段也必须真正关闭():
    checks = [{
        "field_id": "guarantee", "field_name": "对外担保", "category": "relation",
        "section_id": "sec_6", "required": True, "status": "unverified",
        "value": None, "sources": [], "attempted_sources": [],
        "failure_reason": "数据源不覆盖", "conflict_detail": [], "checked_at": "",
    }]
    critic = _Stub()
    critic._ablate_scanner = True
    r = critic.merge_review(
        _state(checks, "经核查，该公司不存在对外担保事项。"),
        _result("pass", 9.0),
    )
    assert not r["issues"], "--ablate scanner 不得在 merge_review 阶段偷偷重新扫描"
    assert r["overall_assessment"]["verdict"] == "pass"


# ---------- 冲突字段的语义边界（BC-26） ----------

def _conflicting_registration():
    return [{
        "field_id": "registration", "field_name": "工商登记信息",
        "category": "basic", "section_id": "sec_1", "required": True,
        "status": "conflicting", "value": None, "sources": [],
        "attempted_sources": [], "failure_reason": "多来源取值冲突",
        "conflict_detail": ["工商信息：5000万元", "财务附注：1500万元"],
        "checked_at": "",
    }]


def test_明确披露互不相容且拒绝选边不应误报():
    text = (
        "实缴资本存在两种互不相容的记载：工商信息为5000万元，"
        "财务报表附注为1500万元。现阶段无法判断哪一项真实，"
        "本报告不据任一口径评价资本实力。"
    )
    assert scan_report(_conflicting_registration(), text) == [], \
        "明确披露来源分歧并拒绝选边是合规处理，不是静默解决冲突"


def test_不同口径与不同记载都属于明确披露冲突():
    for phrase in ("不同口径", "不同记载"):
        text = f"实缴资本存在{phrase}，工商信息为5000万元，财务附注为1500万元。"
        assert scan_report(_conflicting_registration(), text) == [], phrase


def test_到位资本改写后单方面采信仍须检出():
    text = (
        "结合财务资料可确认该公司实际到位资本为1500万元。"
        "工商页面显示的5000万元属于尚未更新的历史口径，不影响本次判断。"
    )
    findings = scan_report(_conflicting_registration(), text)
    assert findings and findings[0]["issue_type"] == "conflict_silently_resolved"
    assert findings[0]["matched_alias"] == "到位资本"


def test_冲突披露词不得豁免普通未核实字段的断言():
    checks = [{
        "field_id": "litigation", "field_name": "涉诉记录",
        "status": "unverified", "failure_reason": "司法源超时",
    }]
    text = "涉诉记录虽存在不同口径，但可以确认该公司无重大诉讼。"
    findings = scan_report(checks, text)
    assert findings and findings[0]["issue_type"] == "unverified_as_fact"


def test_分歧并存两种也属于明确披露冲突():
    """
    BC-26 的同类延续：留出集修完后仍能构造出正确披露却被误判的句子。
    词表是人工维护的，穷举不可能完备——补的是自然中文里表达
    "来源之间对不上"最常用的几个说法。
    """
    for phrase, text in (
        ("分歧", "实缴资本在工商登记与财务附注之间存在分歧，需人工核实。"),
        ("并存", "实缴资本5000万元与1500万元两个记载并存，暂不采信任一方。"),
        ("两种", "实缴资本存在两种记载：工商5000万元、财报1500万元。"),
    ):
        assert scan_report(_conflicting_registration(), text) == [], phrase


def test_存疑不作为冲突披露标记():
    """
    刻意不收「存疑」：它是弱披露，可以与"但现已确认为 X"共存。
    收进白名单会让单方面采信借它绕过检查——
    宁可让这类句子误报一次，也不能给单边采信开口子。
    """
    text = "实缴资本曾存疑，但结合财务资料现已确认为1500万元，据此评估资本实力。"
    findings = scan_report(_conflicting_registration(), text)
    assert findings, "「存疑」+ 单方面采信必须被检出，不得因弱披露词豁免"


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
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
