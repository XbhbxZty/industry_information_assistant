"""
编排等价性断言（v0.6 A 阶段）

## 为什么这个文件必须先于图的重建存在

`graph.py` 里有两套编排：声明式的 `_build_langgraph()` 和命令式的
`_run_simplified()`。运行时只走后者，前者从 349 行起被整段注释掉。

**问题不是"有段死代码"，而是死代码已经和活代码分叉了：**

| | 声明式图 | 实际执行 |
|---|---|---|
| analyze 节点 | 只调 `wizard` | `data_analyst` 然后 `wizard` |
| 风险评分 | **不执行** | `assess_risk()` 在 DataAnalyst 里 |
| 补充搜索回环 | 无 | `re_researching` → scout → writer |
| 企业档案加载 | 无 | `_load_company_profile()` |
| 取消 / 检查点 | 无 | 每个 phase 前后 |

直接"取消注释恢复图执行"会**静默丢掉整个 v0.5 + v0.6a 风险评级链路**——
正是这五轮迭代一直在防的失效形态（BC-15 / BC-31 同形：两套实现只有一套
被执行，另一套的正确性无人验证）。

所以这里先对**当前活着的路径**录一条黄金轨迹，图重建后必须逐事件复现。
换掉编排引擎属于"行为不变的改造"，任何行为差异都必须是被断言过的、
有意为之的差异，而不是重构时掉的东西。

## 方法

六个 Agent 的 `process()` 全部替换为确定性替身：固定条数的 `add_message`
+ 固定的 state 变更。这样轨迹只反映**编排**，不反映模型。
Critic 用脚本控制回环走向，覆盖直线 / 补充搜索 / 修订 / 取消四种路径。

运行：cd backend && python tests/test_graph_equivalence.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2 import graph as graph_module  # noqa: E402
from service.deep_research_v2.graph import DeepResearchGraph  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402

_DD_QUERY = "请对东莞市泰锐精密传动件有限公司做贷前尽职调查，授信2000万元"
_PLAIN_QUERY = "分析一下新能源汽车行业的发展趋势"

# 事件里所有随时间/随机变化的字段。比较轨迹前必须剔除，
# 否则断言会变成时钟测试（BC-35 的教训）。
_VOLATILE = {"timestamp", "session_id", "step_id", "id"}


# ---------------------------------------------------------------- 替身

def _fake_agent(agent, events, mutate=None):
    """
    把一个 Agent 换成确定性替身。

    刻意**保留** `add_message` 的真实实现——被测的正是"消息如何流出去"，
    把它也替掉就等于取消了这条测试（BC-34 的教训：测试替被测对象
    补齐前提，等于没测）。
    """
    async def _process(state):
        for ev_type, content in events:
            agent.add_message(state, ev_type, content)
        if mutate:
            mutate(state)
        return state

    agent.process = _process
    return agent


class _CriticScript:
    """按脚本决定每轮审核后的走向，用来驱动回环"""

    def __init__(self, phases):
        self.phases = list(phases)
        self.calls = 0

    def __call__(self, state):
        phase = self.phases[min(self.calls, len(self.phases) - 1)]
        self.calls += 1
        state["phase"] = phase
        state["iteration"] = state.get("iteration", 0) + 1
        state["quality_score"] = 7.5
        state["unresolved_issues"] = 0 if phase == ResearchPhase.COMPLETED.value else 2


def _build_graph(critic_phases=(ResearchPhase.COMPLETED.value,), saved=None):
    g = DeepResearchGraph.__new__(DeepResearchGraph)     # 跳过 __init__ 的 LLM 客户端构造
    from service.deep_research_v2.agents import (
        ChiefArchitect, DeepScout, CodeWizard, CriticMaster, LeadWriter, DataAnalyst,
    )
    kw = ("sk-test", "http://localhost:1")
    g.llm_api_key, g.llm_base_url, g.search_api_key = "sk-test", "http://localhost:1", "sk-test"
    g.model, g.max_iterations = "test-model", 3

    g.architect = _fake_agent(
        ChiefArchitect(*kw, "m"),
        [("research_step", {"step_type": "planning", "title": "生成尽调提纲"}),
         ("outline_updated", {"sections": 8})],
        lambda s: s.update({"outline": [{"id": f"sec_{i}"} for i in range(1, 9)]}))
    g.scout = _fake_agent(
        DeepScout(*kw, "sk-test", "m"),
        [("research_step", {"step_type": "researching", "title": "多源核查"}),
         ("search_result_item", {"title": "工商登记"})],
        lambda s: s["facts"].append({"id": "f_x", "content": "c", "source_url": "u"}))
    g.data_analyst = _fake_agent(
        DataAnalyst(*kw, "m"),
        [("risk_assessment", {"level": "中风险", "gates_applied": ["g"]})])
    g.wizard = _fake_agent(
        CodeWizard(*kw, "m"),
        [("research_step", {"step_type": "analyzing", "title": "生成图表"})],
        lambda s: s["charts"].append({"id": "c1", "title": "t"}))
    g.writer = _fake_agent(
        LeadWriter(*kw, "m"),
        [("report_chunk", {"content": "报告正文"})],
        lambda s: s.update({"final_report": "# 尽调报告\n正文"}))
    g.critic = _fake_agent(
        CriticMaster(*kw, "m"),
        [("critic_feedback", {"severity": "minor"})],
        _CriticScript(critic_phases))

    # 检查点：不连库，只记录调用，让 checkpoint_saved 事件照常产生
    class _CP:
        def __init__(self): self.statuses = []
        def save_checkpoint(self, **k): return "cp_1"
        def update_status(self, sid, status, err=None): self.statuses.append(status)
        def load_checkpoint(self, sid): return None
        def get_checkpoint_info(self, sid): return None

    g.checkpoint_service = _CP()
    if saved is not None:
        saved.append(g.checkpoint_service)
    g.graph = g._build_langgraph() if graph_module.LANGGRAPH_AVAILABLE else None
    return g


# ---------------------------------------------------------------- 轨迹

def _key(ev):
    """一条事件的可比较指纹：类型 + 最能区分它的那个字段"""
    t = ev.get("type")
    if t == "phase":
        return (t, ev.get("phase"))
    if t == "checkpoint_saved":
        return (t, ev.get("phase"))
    c = ev.get("content")
    if isinstance(c, dict):
        return (t, ev.get("agent"), c.get("step_type") or c.get("level") or None)
    return (t, ev.get("agent"))


def _clean(ev):
    return {k: v for k, v in ev.items() if k not in _VOLATILE}


async def _collect(g, query, cancel_after=None):
    """跑一次并收集事件；cancel_after 用于在第 N 条事件后置取消标志"""
    out = []
    async for ev in g.run(query, "sess-equiv", user_id="u1"):
        out.append(_clean(ev))
        if cancel_after is not None and len(out) == cancel_after:
            graph_module.is_research_cancelled = lambda sid: True
    return out


def _trace(query, critic_phases=(ResearchPhase.COMPLETED.value,), cancel_after=None):
    saved = []
    g = _build_graph(critic_phases, saved)
    original = graph_module.is_research_cancelled
    try:
        evs = asyncio.run(_collect(g, query, cancel_after))
    finally:
        graph_module.is_research_cancelled = original
    return evs, saved[0]


# ---------------------------------------------------------------- 断言

def test_直线流程事件序列():
    """无回环的最短路径。这条轨迹是后续所有比较的基准"""
    evs, _ = _trace(_PLAIN_QUERY)
    keys = [_key(e) for e in evs]

    assert keys[0] == ("research_start", None)
    phases = [e["phase"] for e in evs if e["type"] == "phase"]
    assert phases == ["planning", "researching", "analyzing", "writing", "reviewing"], phases
    assert keys[-1] == ("research_complete", None)

    # 每个阶段结束都要落一次检查点，否则中断后无法恢复
    cps = [e["phase"] for e in evs if e["type"] == "checkpoint_saved"]
    assert cps == [ResearchPhase.INIT.value, ResearchPhase.RESEARCHING.value,
                   ResearchPhase.ANALYZING.value, ResearchPhase.WRITING.value], cps


def test_analyze阶段必须同时跑DataAnalyst和Wizard():
    """
    ⭐ 声明式图当前只在 analyze 节点调 wizard。若按它恢复执行，
    风险评级会整个消失，而所有现有测试都不会红——因为它们直接调
    assess_risk()，从不走编排。这条断言是唯一能发现该缺失的地方。
    """
    evs, _ = _trace(_DD_QUERY)
    agents = [e.get("agent") for e in evs if e["type"] in ("risk_assessment", "research_step")]
    assert "DataAnalyst" in agents, f"analyze 阶段必须跑 DataAnalyst：{agents}"
    assert "CodeWizard" in agents, f"analyze 阶段必须跑 CodeWizard：{agents}"
    assert any(e["type"] == "risk_assessment" for e in evs), "风险评级事件必须出现在编排里"
    # 顺序：评级先于图表（评级不得被 LLM 步骤门控，BC-17）
    types = [e["type"] for e in evs]
    assert types.index("risk_assessment") < max(
        i for i, e in enumerate(evs)
        if e["type"] == "research_step" and (e.get("content") or {}).get("step_type") == "analyzing"
    )


def test_尽调流程注入档案并推清单事件():
    evs, _ = _trace(_DD_QUERY)
    types = [e["type"] for e in evs]
    assert "company_profile_loaded" in types, "识别到尽调对象必须推档案事件"
    assert "field_checks_updated" in types, "核实率是该轮唯一可验收产出，必须推给前端"
    assert types.index("company_profile_loaded") < types.index("phase"), \
        "档案必须在规划之前注入，否则 Architect 拿不到 credit_context"


def test_普通研究流程不推尽调事件():
    evs, _ = _trace(_PLAIN_QUERY)
    types = [e["type"] for e in evs]
    assert "company_profile_loaded" not in types
    assert "field_checks_updated" not in types


def test_补充搜索回环():
    """critic 判 re_researching → scout 再跑一次 → writer 重写"""
    evs, _ = _trace(_DD_QUERY, critic_phases=[
        ResearchPhase.RE_RESEARCHING.value, ResearchPhase.COMPLETED.value])
    phases = [e["phase"] for e in evs if e["type"] == "phase"]
    assert phases == ["planning", "researching", "analyzing", "writing",
                      "reviewing", "re_researching", "rewriting", "reviewing"], phases


def test_修订回环():
    evs, _ = _trace(_DD_QUERY, critic_phases=[
        ResearchPhase.REVISING.value, ResearchPhase.COMPLETED.value])
    phases = [e["phase"] for e in evs if e["type"] == "phase"]
    assert phases == ["planning", "researching", "analyzing", "writing",
                      "reviewing", "revising", "reviewing"], phases


def test_达到最大轮次必须停止():
    """critic 一直要求修订，不能无限循环"""
    evs, _ = _trace(_DD_QUERY, critic_phases=[ResearchPhase.REVISING.value])
    reviews = [e for e in evs if e["type"] == "phase" and e["phase"] == "reviewing"]
    assert len(reviews) <= 3, f"最多 max_iterations 轮，实际 {len(reviews)}"
    assert evs[-1]["type"] == "research_complete", "达到上限也必须给出终局事件"


def test_取消在阶段边界生效():
    evs, _ = _trace(_DD_QUERY, cancel_after=6)
    assert any(e["type"] == "research_cancelled" for e in evs), "取消必须产生显式事件"
    assert evs[-1]["type"] == "research_cancelled", "取消后不得继续推进"
    assert not any(e["type"] == "research_complete" for e in evs), \
        "取消不得产出终局事件——否则下游会以为拿到了完整结论"


def test_终局事件携带全部可验收产出():
    evs, _ = _trace(_DD_QUERY)
    final = evs[-1]
    assert final["type"] == "research_complete"
    for key in ("final_report", "completeness", "field_checks",
                "risk_assessment", "errors", "references"):
        assert key in final, f"终局事件缺 {key}"


def test_完成时更新检查点状态():
    _, cp = _trace(_DD_QUERY)
    assert cp.statuses and cp.statuses[-1] == "completed"


def test_轨迹在两次运行间稳定():
    """替身是确定性的；若同一输入两次轨迹不同，说明编排里混进了非确定性"""
    a, _ = _trace(_DD_QUERY)
    b, _ = _trace(_DD_QUERY)
    assert [_key(e) for e in a] == [_key(e) for e in b]


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
