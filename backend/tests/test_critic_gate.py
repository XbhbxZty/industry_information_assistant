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
