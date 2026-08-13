"""
审核裁决规则测试（BC-29）

## 这一轮在钉什么

Critic 此前自己报 verdict，而"quality_score >= 7 才能 pass"只写在提示词里、
代码从不校验。盲测实测到的形态：模型在 issues 里正确指出了危险外推，
却给 `severity=minor` + `verdict=pass`，报告照常放行。

> **它看见了，然后自己放过了自己。**

修法不是继续调提示词（BC-13/BC-14/BC-47 已经三次证明这条路是过拟合），
而是把裁决权收回代码：模型仍产出 issues，规则决定能不能过。

## 为什么这不是第二个扫描器

扫描器是拿正则做**开集检测**——中文里有多少种把未核实写成无记录的说法
没有边界，词表永远补不完。本模块是对**已抽取的结构化数据**做**闭集规则**，
输入是 issues 列表，输出三选一。没有文本匹配，可穷举、零方差。

运行：cd backend && python tests/test_review_verdict.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from service.review_verdict import (  # noqa: E402
    DD_BLOCKING_ISSUE_TYPES, MIN_PASS_SCORE, VERDICT_MAJOR_ISSUES,
    VERDICT_NEEDS_REVISION, VERDICT_PASS, derive_verdict, is_blocking,
    unresolved_blocking_issues,
)


def _issue(t="missing_source", sev="minor", **kw):
    return {"issue_type": t, "severity": sev, "description": "x", **kw}


# ---------------------------------------------------------------- 基本判据

def test_无问题且分数达标时通过():
    r = derive_verdict([], 8.0, llm_verdict="pass")
    assert r["verdict"] == VERDICT_PASS
    assert not r["llm_verdict_overridden"]
    assert r["verdict_reasons"], "即便通过也要说明依据"


def test_分数不达标不得通过():
    """此前这条只写在提示词里，代码从不校验——BC-29 的直接成因之一"""
    r = derive_verdict([], 6.9, llm_verdict="pass")
    assert r["verdict"] == VERDICT_NEEDS_REVISION
    assert r["llm_verdict_overridden"], "模型说 pass，规则说不行，必须记为改写"
    assert any(str(MIN_PASS_SCORE) in x for x in r["verdict_reasons"])


def test_分数非数值按零处理():
    """LLM 曾返回过 -1 与字符串；解析失败不得变成"没问题" """
    for bad in (None, "很好", "", [], {}):
        assert derive_verdict([], bad)["verdict"] != VERDICT_PASS, bad


def test_critical问题阻断():
    r = derive_verdict([_issue("logic_error", "critical")], 9.0)
    assert r["verdict"] == VERDICT_MAJOR_ISSUES


def test_major问题需要修订():
    r = derive_verdict([_issue("missing_source", "major")], 9.0)
    assert r["verdict"] == VERDICT_NEEDS_REVISION


def test_仅有minor且分数达标可通过():
    """规则不能过严到把一切都拦下——那样它同样失去信息量"""
    r = derive_verdict([_issue("bias", "minor"), _issue("outdated", "minor")], 8.5)
    assert r["verdict"] == VERDICT_PASS


# ------------------------------------------- ⭐ BC-29 本体：核心类型不许降级

def test_尽调核心问题标minor也不得通过():
    """
    ⭐ BC-29 的复现与修复验证。

    模型指出了 unverified_as_fact，却标 minor 并自报 pass。
    允许 severity 决定这三类是否阻断，等于把裁决权又交回给
    刚刚被证明会放水的那一方。
    """
    for t in ("unverified_as_fact", "conflict_silently_resolved",
              "unsupported_risk_conclusion"):
        r = derive_verdict([_issue(t, "minor")], 9.0, llm_verdict="pass")
        assert r["verdict"] == VERDICT_MAJOR_ISSUES, f"{t} 标 minor 仍必须阻断"
        assert r["llm_verdict_overridden"]
        assert any(t in x and "不接受严重度降级" in x for x in r["verdict_reasons"]), \
            f"必须说清为何阻断：{r['verdict_reasons']}"


def test_核心类型清单与反幻觉架构对齐():
    """这三类正是 v0.3 起 Critic 专项检测的对象；漏一类就漏一条防线"""
    assert {"unverified_as_fact", "conflict_silently_resolved",
            "unsupported_risk_conclusion"} <= DD_BLOCKING_ISSUE_TYPES


def test_审核未执行必然阻断():
    """降级路径产出的 review_not_executed 必须走同一套规则得出阻断"""
    r = derive_verdict([_issue("review_not_executed", "critical")], 0.0)
    assert r["verdict"] == VERDICT_MAJOR_ISSUES


# ---------------------------------------------------------------- 留痕

def test_模型原始判断必须保留():
    """
    与人工复核改写等级同理（BC-22 / v0.6）：
    谁做的判断、依据什么，事后必须能分辨。
    """
    r = derive_verdict([_issue("unverified_as_fact", "minor")], 9.0, llm_verdict="pass")
    assert r["llm_verdict"] == "pass", "模型自报的判断不得被抹掉"
    assert r["verdict_source"] == "rule"
    assert r["llm_verdict_overridden"] is True


def test_未提供模型判断时不算改写():
    r = derive_verdict([], 8.0)
    assert r["llm_verdict"] is None
    assert r["llm_verdict_overridden"] is False


def test_裁决可复现():
    """纯函数：同一输入必须完全一致（对比 LLM 自裁的方差）"""
    args = ([_issue("unverified_as_fact", "minor"), _issue("bias", "major")], 5.0, "pass")
    a, b = derive_verdict(*args), derive_verdict(*args)
    assert a == b


def test_脏数据不会让规则崩溃():
    """LLM 返回结构不规范是常态，规则必须能容错且倒向保守"""
    dirty = [None, "字符串", 42, {}, {"issue_type": None}, {"severity": "unknown"}]
    r = derive_verdict(dirty, 9.0)
    assert r["verdict"] == VERDICT_PASS, "无法识别的条目不虚构问题"
    assert derive_verdict(None, 3.0)["verdict"] != VERDICT_PASS, "分数低仍须拦"


def test_is_blocking语义():
    assert not is_blocking(VERDICT_PASS)
    assert is_blocking(VERDICT_NEEDS_REVISION)
    assert is_blocking(VERDICT_MAJOR_ISSUES)


def test_unresolved_blocking只挑核心类型():
    issues = [_issue("unverified_as_fact", "minor"), _issue("bias", "critical"),
              _issue("conflict_silently_resolved", "major")]
    got = unresolved_blocking_issues(issues)
    assert {i["issue_type"] for i in got} == {
        "unverified_as_fact", "conflict_silently_resolved"}


# ------------------------------------------- 生产路径：merge_review 应用规则

def _critic():
    from service.deep_research_v2.agents.critic import CriticMaster
    return CriticMaster("sk-test", "http://localhost:1", "test-model")


def test_生产路径的裁决来自规则而非模型():
    """
    BC-15 的教训：规范化必须收敛在 merge_review，评测与生产走同一路径。
    这条断言确认规则确实在那条路径上生效。
    """
    llm_said = {
        "overall_assessment": {"quality_score": 9, "verdict": "pass",
                               "summary": "整体良好"},
        "issues": [_issue("unverified_as_fact", "minor")],
    }
    out = _critic().merge_review({}, llm_said)
    oa = out["overall_assessment"]
    assert oa["verdict"] == VERDICT_MAJOR_ISSUES, "模型说 pass，规则必须推翻"
    assert oa["llm_verdict"] == "pass"
    assert oa["verdict_source"] == "rule"
    assert oa["verdict_reasons"]


def test_降级路径同样走规则裁决():
    out = _critic().merge_review({}, None)
    assert out["degraded"] is True
    assert out["overall_assessment"]["verdict"] == VERDICT_MAJOR_ISSUES
    assert out["overall_assessment"]["verdict_source"] == "rule", \
        "降级路径不得绕过规则另写一套裁决"


def test_合法通过的报告不被规则误伤():
    llm_said = {
        "overall_assessment": {"quality_score": 8, "verdict": "pass", "summary": "好"},
        "issues": [_issue("bias", "minor")],
    }
    oa = _critic().merge_review({}, llm_said)["overall_assessment"]
    assert oa["verdict"] == VERDICT_PASS
    assert oa["llm_verdict_overridden"] is False


# -------------------------- ⭐ BC-29 的另一半：迭代用尽不等于问题解决

def _post_review_state(issues, iteration=3, max_iterations=3):
    """
    构造"审核刚跑完、正要决定下一步"的 state。

    直接驱动 CriticMaster.process() 会真调 LLM；这里复用它的下游分支——
    把 merge_review 的产出与迭代计数摆好，调用被测的路由逻辑。
    """
    import asyncio
    from service.deep_research_v2.agents.critic import CriticMaster
    from service.deep_research_v2.state import ResearchPhase

    agent = CriticMaster("sk-test", "http://localhost:1", "test-model")
    review = agent.merge_review({}, {
        "overall_assessment": {"quality_score": 4, "verdict": "needs_revision"},
        "issues": issues,
    })

    state = {
        "field_checks": [], "final_report": "报告正文", "draft_sections": {},
        "errors": [], "messages": [], "iteration": iteration,
        "max_iterations": max_iterations, "quality_score": 0.0,
        "unresolved_issues": 0, "pending_search_queries": [],
        "critic_feedback": [], "facts": [], "logs": [],
        "phase": ResearchPhase.REVIEWING.value,
        "risk_assessment": {
            "level": "中风险", "requires_human_review": False,
            "gates_applied": [], "composite_score": 30.0,
        },
    }

    async def _fake_review(*a, **k):
        return review

    agent._review_content = _fake_review
    asyncio.run(agent.process(state))
    return state


def test_迭代用尽仍有阻断问题时强制人工复核():
    """
    ⭐ 此前这条分支直接进完成态，一份带着未解决 unverified_as_fact 的报告
    就这样出厂了——只留下一句 warning，而 warning 不参与任何判定。
    与 BC-33 同形：披露不是控制。
    """
    state = _post_review_state([_issue("unverified_as_fact", "critical")])
    ra = state["risk_assessment"]
    assert ra["requires_human_review"] is True, "迭代用尽不等于问题解决，必须转人工"
    assert any("迭代已用尽" in g for g in ra["gates_applied"]), \
        f"必须作为闸门留痕：{ra['gates_applied']}"
    assert any("审核未收敛" in e for e in state["errors"]), "须显式披露未收敛"


def test_迭代用尽但无阻断问题时不额外转人工():
    """规则不能过严：只有 minor 的报告不该被强制人工复核"""
    state = _post_review_state([_issue("bias", "minor")])
    assert state["risk_assessment"]["requires_human_review"] is False
    assert not any("迭代已用尽" in g for g in state["risk_assessment"]["gates_applied"])


def test_未到迭代上限时不走强制完成分支():
    from service.deep_research_v2.state import ResearchPhase
    state = _post_review_state([_issue("unverified_as_fact", "critical")],
                               iteration=1, max_iterations=3)
    assert state["phase"] != ResearchPhase.COMPLETED.value, \
        "还有迭代余量时应继续修订/补搜，而不是强制完成"


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
            print(f"  FAIL  {name}: {str(e)[:160]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
