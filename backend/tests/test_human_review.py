"""
人机协同复核的行为断言（v0.6 B 阶段）

## 这一轮在钉什么

业务约束：**高风险结论不得全自动放行**。这既是信贷合规要求，也是出坏账
追责的前提——报告上必须有人签字。

`checkpoint_service` 从原项目起就支持 `paused` 状态，但在 v0.6 之前
**没有任何一行代码设置过它**。这一轮让它第一次真正被设置。

## 断言落在哪

与 v0.6a 的教训一致（BC-31：断言停在中间报告上等于没有断言），
这里每条都落在**可观测的外部行为**上：

    暂停了吗 / 终局事件发了吗 / 检查点状态是什么 /
    复核结论进报告正文了吗 / 规则引擎的原始等级还在吗

最关键的一条是「暂停时不得发 `research_complete`」：
暂停意味着这份结论**还没有效力**，若终局事件照发，
下游会以为拿到了完整可用的授信结论。

运行：cd backend && python tests/test_human_review.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from config.verification_policy import POLICY  # noqa: E402
from service.deep_research_v2.graph import reset_graph_checkpointer  # noqa: E402
from service.risk_scorecard import (  # noqa: E402
    INSUFFICIENT, LEVELS, RISK_BLOCK_MARKER, apply_human_review,
    needs_human_review, render_markdown,
)
from test_graph_equivalence import (  # noqa: E402
    _DD_QUERY, _assessment, _build_graph, _clean, _trace,
)

_APPROVE = {"approved": True, "reviewer": "风控-张三", "comment": "已核对司法源，同意授信"}
_REJECT = {"approved": False, "reviewer": "风控-李四", "comment": "担保圈未查清，退回补充"}
_DOWNGRADE = {"approved": True, "reviewer": "风控-王五",
              "comment": "实地走访确认经营正常，下调一级", "override_level": "低风险"}


def _types(evs):
    return [e["type"] for e in evs]


def _one(evs, t):
    hits = [e for e in evs if e["type"] == t]
    assert hits, f"缺少 {t} 事件，实际：{_types(evs)}"
    return hits[0]


# ================================================== 纯函数：复核结论并入评级

def test_未评级时必须复核():
    """连评级都没有，更不能自动放行"""
    assert needs_human_review(None) is True
    assert needs_human_review({}) is True


def test_评级自身决定是否需要复核():
    assert needs_human_review(_assessment(requires_review=True)) is True
    assert needs_human_review(_assessment(requires_review=False)) is False


def test_复核结论必须署名():
    """没有复核人的确认等于没有复核——出坏账时无从追责"""
    for bad in ({}, {"approved": True}, {"approved": True, "reviewer": "  "}):
        try:
            apply_human_review(_assessment(True), bad)
            raise AssertionError(f"未署名的结论不应被接受：{bad}")
        except ValueError as e:
            assert "署名" in str(e)


def test_非法等级被拒绝():
    try:
        apply_human_review(_assessment(True), {**_APPROVE, "override_level": "特别低风险"})
        raise AssertionError("非法等级不应被接受")
    except ValueError as e:
        assert "override_level" in str(e)


def test_人工下调等级必须保留规则引擎原始结论():
    """
    ⭐ 复核人可以推翻规则——业务上必需。但改写必须留痕：
    改写而不留痕，追责时无法区分"规则算错了"和"人改过了"。
    与 BC-22 同形，当时是模型改写等级，这次是人。
    """
    out = apply_human_review(_assessment(True, level="高风险"), _DOWNGRADE)
    assert out["level"] == "低风险", "复核人的调整必须生效"
    assert out["human_review"]["engine_level"] == "高风险", "规则引擎原始结论必须保留"
    assert any("高风险" in g and "低风险" in g for g in out["gates_applied"]), \
        f"调整必须作为闸门留痕：{out['gates_applied']}"
    assert "王五" in " ".join(out["gates_applied"]), "闸门必须记下是谁改的"
    assert out["credit_advice"] == "可考虑核准授信", "授信建议须随调整后的等级更新"


def test_复核未通过时不得出具授信建议():
    out = apply_human_review(_assessment(True), _REJECT)
    assert not out["human_review"]["approved"]
    assert "不得出具授信建议" in out["credit_advice"]
    assert any("未通过" in g for g in out["gates_applied"])


def test_复核通过但不调整等级时保持规则结论():
    out = apply_human_review(_assessment(True, level="中风险"), _APPROVE)
    assert out["level"] == "中风险"
    assert out["human_review"]["override_level"] is None
    assert out["human_review"]["engine_level"] == "中风险"


def test_已复核不把requires_human_review改回假():
    """该字段描述的是「曾经必须复核」，不是「还没复核」。改回假会让历史无法追溯"""
    out = apply_human_review(_assessment(True), _APPROVE)
    assert out["requires_human_review"] is True
    assert out["human_review"]["completed"] is True


def test_复核结论渲染进评级块且与规则结论并列():
    out = apply_human_review(_assessment(True, level="高风险"), _DOWNGRADE)
    block = render_markdown(out)
    assert "人工复核" in block
    assert "风控-王五" in block
    assert "高风险" in block, "规则引擎原始等级必须出现在报告里"
    assert "低风险" in block, "调整后等级也必须出现"
    assert "未通过" not in block


def test_未通过的复核在报告里明确标出():
    block = render_markdown(apply_human_review(_assessment(True), _REJECT))
    assert "**未通过**" in block


# ================================================== 编排：中断与恢复

def test_要求复核时暂停且不得发终局事件():
    """
    ⭐ 本轮最重要的一条。

    暂停意味着这份结论**还没有效力**。若终局事件照发，
    只等 research_complete 的调用方会以为拿到了可用的授信结论。
    """
    evs, _ = _trace(_DD_QUERY, requires_review=True)
    assert "human_review_required" in _types(evs), f"必须推出复核请求：{_types(evs)}"
    assert "research_complete" not in _types(evs), \
        "暂停时绝不能发终局事件——下游会以为结论已生效"
    assert evs[-1]["type"] == "human_review_required", "暂停后不得继续推进"


def test_暂停时检查点状态置为paused():
    """`paused` 状态从原项目起就存在，这是第一次真正被设置"""
    _, cp = _trace(_DD_QUERY, requires_review=True)
    assert "paused" in cp.statuses, f"实际状态流转：{cp.statuses}"
    assert "completed" not in cp.statuses, "暂停不等于完成"


def test_复核请求携带复核人判断所需的材料():
    """
    只给等级不够：等级往往由闸门而非分数决定，
    只给分数会让复核人得出与等级相反的结论。
    """
    evs, _ = _trace(_DD_QUERY, requires_review=True, raw=True)
    req = _one(evs, "human_review_required")
    for key in ("level", "composite_score", "gates_applied", "credit_advice",
                "verified_rate", "unverified_fields", "conflicting_fields",
                "critical_issues", "company_name", "session_id"):
        assert key in req, f"复核请求缺少 {key}"
    assert req["gates_applied"], "闸门必须给出——等级往往由它决定"


def test_不要求复核时不暂停直接完成():
    evs, cp = _trace(_DD_QUERY, requires_review=False)
    assert "human_review_required" not in _types(evs)
    assert evs[-1]["type"] == "research_complete"
    assert "paused" not in cp.statuses


def test_提交复核结论后从断点继续并完成():
    evs, cp = _trace(_DD_QUERY, requires_review=True, resume_with=_APPROVE)
    types = _types(evs)
    assert "human_review_required" in types
    assert "research_resumed" in types
    assert "human_review_completed" in types
    assert types[-1] == "research_complete", f"恢复后必须走到终局：{types[-3:]}"
    assert cp.statuses == ["paused", "completed"], f"状态流转应为暂停→完成：{cp.statuses}"


def test_恢复后终局事件携带复核结论():
    evs, _ = _trace(_DD_QUERY, requires_review=True, resume_with=_APPROVE)
    final = evs[-1]
    hr = final["risk_assessment"]["human_review"]
    assert hr["completed"] and hr["approved"]
    assert hr["reviewer"] == "风控-张三"
    assert hr["reviewed_at"], "复核时间必须留痕"


def test_人工调整的等级贯通到终局事件():
    evs, _ = _trace(_DD_QUERY, requires_review=True, resume_with=_DOWNGRADE)
    ra = evs[-1]["risk_assessment"]
    assert ra["level"] == "低风险", "复核人的调整必须一路走到终局"
    assert ra["human_review"]["engine_level"] == "中风险", "规则引擎原始结论必须还在"
    assert any("中风险" in g and "低风险" in g for g in ra["gates_applied"])


def test_复核结论写回报告正文():
    """
    复核人签的是这份报告，结论就必须出现在这份报告里——
    只存在事件载荷里，导出 Word 交到评审会时就看不到谁批的。
    """
    evs, _ = _trace(_DD_QUERY, requires_review=True, resume_with=_APPROVE)
    report = evs[-1]["final_report"]
    assert RISK_BLOCK_MARKER in report, "报告必须含规则引擎评级块"
    assert "人工复核" in report and "风控-张三" in report, "复核结论必须进正文"


def test_未署名的复核结论不被静默采纳():
    """非法结论宁可停在未复核状态，也不能当作已复核放行"""
    evs, _ = _trace(_DD_QUERY, requires_review=True,
                    resume_with={"approved": True, "comment": "忘了填名字"})
    final = evs[-1]
    assert final["type"] == "research_complete"
    assert not (final["risk_assessment"].get("human_review") or {}).get("completed"), \
        "非法结论不得被记为已复核"
    assert any("复核结论非法" in e for e in final["errors"]), \
        f"必须显式披露结论被拒：{final['errors']}"


def test_没有中断点时恢复给出明确错误():
    g = _build_graph(requires_review=False)

    async def _go():
        return [e async for e in g.resume_review("从未存在的会话", _APPROVE)]

    evs = asyncio.run(_go())
    assert evs and evs[0]["type"] == "error"
    assert "没有待复核的中断点" in evs[0]["content"]


def test_中断点可被另一个图实例恢复():
    """
    ⭐ 决定 checkpointer 选型的那条约束。

    `DeepResearchV2Service()` 每次请求新建，风控人员几小时后来点确认时，
    面对的是一个全新的图对象。若检查点只活在进程内存里，那个断点已经不在了。
    这条断言用「两个图实例共享同一个 checkpointer」模拟跨请求恢复。
    """
    saver = MemorySaver()
    reset_graph_checkpointer(saver)
    g1 = _build_graph(requires_review=True)
    reset_graph_checkpointer(saver)          # _build_graph 会重置，这里放回同一个
    g1.graph = g1._build_langgraph()
    session = "sess-cross-instance"

    async def _first():
        return [_clean(e) async for e in g1.run(_DD_QUERY, session, user_id="u1")]

    evs1 = asyncio.run(_first())
    assert evs1[-1]["type"] == "human_review_required"

    reset_graph_checkpointer(saver)
    g2 = _build_graph(requires_review=True)  # 全新实例，等价于新的一次请求
    reset_graph_checkpointer(saver)
    g2.graph = g2._build_langgraph()

    async def _second():
        return [_clean(e) async for e in g2.resume_review(session, _APPROVE, user_id="u1")]

    evs2 = asyncio.run(_second())
    assert evs2[-1]["type"] == "research_complete", \
        f"新实例必须能恢复同一个断点：{_types(evs2)}"
    assert evs2[-1]["risk_assessment"]["human_review"]["reviewer"] == "风控-张三"


def test_配置可关闭复核卡点仅供离线评测():
    """
    评测跑几十家企业，没人在旁边点确认，中断会让整批任务挂死。
    但生产环境关掉它等于取消了复核这道岗——所以开关必须在统一配置里显式可见。
    """
    original = POLICY.require_human_review_gate
    try:
        POLICY.require_human_review_gate = False
        evs, cp = _trace(_DD_QUERY, requires_review=True)
        assert "human_review_required" not in _types(evs)
        assert evs[-1]["type"] == "research_complete"
        assert "paused" not in cp.statuses
    finally:
        POLICY.require_human_review_gate = original
    assert POLICY.require_human_review_gate is True, "默认必须开着"


def test_复核卡点不影响取消():
    evs, _ = _trace(_DD_QUERY, requires_review=True, cancel_after=6)
    types = _types(evs)
    assert "research_cancelled" in types
    assert "human_review_required" not in types, "已取消的任务不该再要求人工复核"
    assert "research_complete" not in types


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
