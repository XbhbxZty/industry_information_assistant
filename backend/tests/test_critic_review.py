"""
Critic 审核结果规范化与降级路径测试

本文件由 `test_critic_gate.py` 演化而来。原文件的主体是"扫描器 critical 门控"，
而确定性扫描器已随 BC-47 删除，那批断言连同被测对象一并移除。

保留并强化的是**降级语义**，它比门控更根本：

    审核未执行  ≠  审核通过

原设计里 LLM 不可用时退回"仅由扫描器审核"，产出 `needs_revision` + 5.0 分。
扫描器删除后没有第二条链路，因此改为 fail-closed：返回 `major_issues` + 0.0 分，
并显式给出一条 `review_not_executed` 的 critical，让编排层拒绝进入完成态。

这与完整度闸门、证据链校验的方向一致——**最后一道关，后面没人接，宁可不出结论**。
（扫描器当初可以取相反的默认，正因为它后面还有 LLM；那个前提现在不存在了。）

这类缺陷不会让任何数字变难看——报告照常产出、分数照常显示，只有断言能把它们钉住。

运行：cd backend && python tests/test_critic_review.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.critic import CriticMaster  # noqa: E402


class _Stub(CriticMaster):
    """只借用 merge_review / _content_for_review，不做真实 LLM 调用"""
    def __init__(self):
        import logging
        self.logger = logging.getLogger("stub")


def _state(checks=None, text=""):
    return {"field_checks": checks or [], "final_report": text,
            "draft_sections": {}, "errors": []}


def _result(verdict="pass", score=9.0, issues=None):
    return {"overall_assessment": {"verdict": verdict, "quality_score": score},
            "issues": issues if issues is not None else []}


_UNVERIFIED_GUARANTEE = [{
    "field_id": "guarantee", "field_name": "对外担保", "category": "relation",
    "section_id": "sec_6", "required": True, "status": "unverified",
    "value": None, "sources": [], "attempted_sources": [],
    "failure_reason": "数据源不覆盖", "conflict_detail": [], "checked_at": "",
}]


# ---------- 降级判定：三种失败形态都要认出来 ----------

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


# ---------- fail-closed：审核未执行不得表现为审核通过 ----------

def test_降级时裁决为最严且分数归零():
    """
    扫描器删除后没有兜底链路。此时 5.0 分 + needs_revision 是危险的中间态：
    下游若按分数放行，一次 API 故障就等于一次无人复核的授信。
    """
    r = _Stub().merge_review(_state(_UNVERIFIED_GUARANTEE, "任意正文"), None)
    assert r["overall_assessment"]["verdict"] == "major_issues"
    assert r["overall_assessment"]["quality_score"] == 0.0


def test_降级时必须给出未审核的显式问题():
    """
    只把 verdict 压低不够——issues 为空会让人误读成"审过了，没发现问题"。
    必须留下一条可读的 critical 说明审核根本没执行。
    """
    r = _Stub().merge_review(_state(), None)
    types = [i.get("issue_type") for i in r["issues"]]
    assert "review_not_executed" in types
    crit = [i for i in r["issues"] if i.get("severity") == "critical"]
    assert crit, "未执行审核必须是 critical，否则不会阻断流程"
    assert "未经" in crit[0]["description"] or "未经复核" in r["overall_assessment"]["summary"]


def test_降级摘要必须说明不得作为授信依据():
    """摘要是人会读的那一行，不能只写技术原因"""
    s = _Stub().merge_review(_state(), None)["overall_assessment"]["summary"]
    assert "未" in s and ("授信" in s or "复核" in s)


def test_降级不因清单为空而变宽松():
    """没有核查清单不代表没有风险——两种降级必须一样严"""
    a = _Stub().merge_review(_state([], ""), None)["overall_assessment"]
    b = _Stub().merge_review(_state(_UNVERIFIED_GUARANTEE, "正文"), None)["overall_assessment"]
    assert a["verdict"] == b["verdict"] == "major_issues"
    assert a["quality_score"] == b["quality_score"] == 0.0


# ---------- LLM 结论的规范化 ----------

def test_有效结果按原样返回不被改写():
    """
    删除门控后，Critic 不再有任何"程序否决模型裁决"的路径。
    这是有意的：三次盲测证明那条路径唯一一次生效是拦下了正确的报告。
    """
    r = _Stub().merge_review(_state(), _result("pass", 9.0))
    assert r["overall_assessment"]["verdict"] == "pass"
    assert r["overall_assessment"]["quality_score"] == 9.0


def test_三类清单问题不再被程序丢弃():
    """
    ⚠️ BC-47 的核心回归。原先扫描器"独占" unverified_as_fact 与
    conflict_silently_resolved，`merge_review` 会丢弃 LLM 输出的同类问题。
    盲测显示这直接造成 4 例正确检出被扔掉——扫描器没命中，LLM 命中了却不算。
    """
    issues = [
        {"issue_type": "unverified_as_fact", "severity": "critical", "description": "x"},
        {"issue_type": "conflict_silently_resolved", "severity": "critical", "description": "y"},
        {"issue_type": "unsupported_risk_conclusion", "severity": "major", "description": "z"},
    ]
    r = _Stub().merge_review(_state(), _result("major_issues", 2.0, issues))
    got = {i["issue_type"] for i in r["issues"]}
    assert got == {"unverified_as_fact", "conflict_silently_resolved",
                   "unsupported_risk_conclusion"}, "LLM 的清单类判定不得再被程序丢弃"


def test_issue_标注来源便于事后归因():
    r = _Stub().merge_review(
        _state(), _result("needs_revision", 5.0,
                          [{"issue_type": "hallucination", "severity": "major"}]))
    assert r["issues"][0]["detected_by"] == "llm"


def test_字符串形态的issue不导致崩溃():
    """关闭清单的消融组里模型会返回字符串 issue，规范化不得炸"""
    r = _Stub().merge_review(_state(), {"issues": ["报告存在幻觉"],
                                        "overall_assessment": {"verdict": "needs_revision"}})
    assert not r.get("degraded")


# ---------- 待审文本同源 ----------

def test_优先审核最终报告():
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


# ---------- 扫描器确已移除 ----------

def test_扫描器模块与门控已不存在():
    """
    删除必须是真删除。留一个未接线的 `scan_report` 或 `_enforce_scanner_gate`
    会让下一个人以为它还在工作——BC-41（声明与实际分叉）就是这么来的。
    """
    import importlib
    for mod in ("service.claim_scanner", "app.service.claim_scanner"):
        try:
            importlib.import_module(mod)
        except ImportError:
            continue
        raise AssertionError(f"{mod} 仍可导入，扫描器未真正删除")
    assert not hasattr(CriticMaster, "_enforce_scanner_gate")
    assert "scanner_findings" not in CriticMaster.REVIEW_PROMPT


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
            print(f"  FAIL  {name}: {str(e)[:120]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
