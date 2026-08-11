"""
风险评分卡接入主流程的**行为断言**

test_risk_scorecard.py 测的是"评分卡算得对不对"，本文件测的是
"评级有没有真的生效"——BC-17 的教训是这两件事完全不同：
扫描器曾经检出违规却不影响裁决，机制实现了，但对结果没有约束力。

因此这里的每条断言都指向一个可观测的外部行为：
    评级是否进了 state / 是否推了 SSE / 是否进了报告正文 /
    LLM 挂掉时是否仍然存在 / 前置条件缺失时是否 fail-closed。

运行：cd backend && python tests/test_risk_integration.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service import risk_scorecard  # noqa: E402
from service.claim_scanner import scan_report  # noqa: E402
from service.company_profile import fill_field_checks, profile_to_facts  # noqa: E402
from service.risk_scorecard import INSUFFICIENT, LEVELS, RISK_BLOCK_MARKER  # noqa: E402
from service.deep_research_v2.agents import data_analyst as da_module  # noqa: E402
from service.deep_research_v2.agents.data_analyst import DataAnalyst  # noqa: E402
from service.deep_research_v2.agents.writer import LeadWriter  # noqa: E402
from service.deep_research_v2.graph import build_complete_event  # noqa: E402
from service.deep_research_v2.state import ResearchPhase, create_initial_state  # noqa: E402

# 一家有失信记录的企业：等级必然被一票否决类闸门顶到高风险及以上，
# 便于断言"报告里的等级确实来自规则引擎"
_COMPANY = {
    "name": "测试科技有限公司",
    "registration": {"operating_status": "存续"},
    "financials": [{"period": "2025年度", "revenue": 9800.0, "net_profit": -2150.0,
                    "debt_ratio": 0.891, "operating_cash_flow": -2870.0}],
    "judicial_records": [{"type": "失信", "amount": 1200},
                         {"type": "被执行", "amount": 486}],
    "negative_news": [], "guarantee": [],
    "bidding_records": [{"project": "x", "amount": 100.0, "win_date": "2025-01-01"}],
}


def _checks(status_map=None):
    cs = build_field_checks(checked_at="2026-08-11T00:00:00")
    for c in cs:
        c["status"] = (status_map or {}).get(c["field_id"], "verified")
        if c["status"] == "verified":
            c["value"] = "经查询，无相关记录"
    return cs


def _dd_state(company=_COMPANY, status_map=None):
    """构造一个已注入尽调对象的 state（等价于 graph._load_company_profile 之后）"""
    state = create_initial_state("请对测试科技有限公司做贷前尽职调查", "test-session")
    state["company_name"] = company.get("name", "")
    state["company_profile"] = company
    # facts 必须有内容：DataAnalyst 的三个 LLM 步骤在无素材时会直接返回，
    # 空 facts 会让"LLM 失败"用例根本走不到 LLM
    state["facts"] = profile_to_facts(company)
    state["field_checks"] = _checks(status_map)
    state["completeness"] = compute_completeness(state["field_checks"])
    state["phase"] = ResearchPhase.ANALYZING.value
    return state


def _analyst(llm_raises=True):
    agent = DataAnalyst("sk-test", "http://localhost:1", "test-model")

    async def _boom(*a, **k):
        raise RuntimeError("LLM 不可用")

    if llm_raises:
        agent.call_llm = _boom
    return agent


def _writer(response_text: str, captured: list = None):
    agent = LeadWriter("sk-test", "http://localhost:1", "test-model")

    async def _fake(system_prompt=None, user_prompt=None, **k):
        if captured is not None:
            captured.append(user_prompt)
        return response_text

    agent.call_llm = _fake
    return agent


def _events(state, event_type):
    return [m for m in state["messages"] if m.get("type") == event_type]


# ---------- ⭐ 核心：评级不得被 LLM 成败门控（BC-17）----------

def test_LLM全部失败时评级仍然产出():
    """
    DataAnalyst 的三个 LLM 步骤全挂，评级必须已经存在。
    这是 BC-17 的同款陷阱：把确定性组件写在不确定组件之后/之内。
    """
    state = _dd_state()
    agent = _analyst(llm_raises=True)

    raised = False
    try:
        asyncio.run(agent.process(state))
    except Exception:
        raised = True   # 生产中由 graph.run_agent_with_streaming 吞掉

    assert raised, "前提：本用例要求 LLM 路径确实失败，否则测的不是这件事"
    assert state["charts"] == [], "前提：LLM 挂了就不该有图表"
    assert state["risk_assessment"], "LLM 失败不得影响纯规则评级的产出"
    assert LEVELS.index(state["risk_assessment"]["level"]) >= LEVELS.index("高风险")


def test_评级以SSE事件推送且带闸门与规则():
    state = _dd_state()
    _analyst().assess_risk(state)

    evs = _events(state, "risk_assessment")
    assert len(evs) == 1, "评级必须推一条 SSE 事件，否则前端与调用方看不到"
    c = evs[0]["content"]
    for key in ("level", "composite_score", "gates_applied", "triggered_rules",
                "requires_human_review", "credit_advice"):
        assert key in c, f"SSE 事件缺少 {key}"
    assert c["level"] == state["risk_assessment"]["level"]
    assert c["gates_applied"], "本用例存在失信记录，闸门必须留痕"


def test_research_complete携带风险评估():
    """
    只在中途推流是不够的：只等最终事件的调用方必须也能拿到等级。
    """
    state = _dd_state()
    _analyst().assess_risk(state)
    ev = build_complete_event(state, [])
    assert ev["risk_assessment"], "终局事件必须带评级"
    assert ev["risk_assessment"]["level"] == state["risk_assessment"]["level"]
    assert "gates_applied" in ev["risk_assessment"], "只给分数会让下游得出相反结论"


# ---------- ⭐ 前置条件不满足时 fail-closed ----------

def test_档案缺失时不得判为低风险():
    """
    清单说 verified、但结构化档案没进 state —— 评分卡会把"档案里没有被执行记录"
    读成"未发现被执行记录"，凭空造出一个正面结论。
    这是接入环节独有的失效路径（评分卡单测覆盖不到），必须 fail-closed。
    """
    # 先证明危险确实存在：直接调 score() 会给出"低风险"
    cs = _checks()
    naive = risk_scorecard.score({}, cs, compute_completeness(cs))
    assert naive["level"] == "低风险", f"前提：裸调用会误判为低风险（实际 {naive['level']}）"

    state = _dd_state()
    state["company_profile"] = {}
    r = _analyst().assess_risk(state)

    assert r["level"] == INSUFFICIENT, "档案缺失必须落到不可评级，而不是低风险"
    assert r["requires_human_review"] is True
    assert any("档案" in g for g in r["gates_applied"]), "必须说明为什么不予评级"
    assert state["errors"], "链路缺陷不得静默"


def test_评分执行失败时fail_closed():
    """算不出 ≠ 没风险。异常不能让评级消失，也不能变成一个低分"""
    state = _dd_state()
    orig = da_module.score_risk

    def _boom(*a, **k):
        raise ValueError("模拟打分异常")

    da_module.score_risk = _boom
    try:
        r = _analyst().assess_risk(state)
    finally:
        da_module.score_risk = orig

    assert r["level"] == INSUFFICIENT
    assert r["requires_human_review"] is True
    assert state["risk_assessment"]["level"] == INSUFFICIENT
    assert _events(state, "risk_assessment"), "降级结论同样要推送，不得静默"
    assert state["errors"]


def test_非尽调流程不产出评级():
    """无核查清单 = 没有授信结论这个产物，此时不该硬造一个'数据不足'的评级"""
    state = create_initial_state("2025年新能源行业趋势", "s2")
    state["phase"] = ResearchPhase.ANALYZING.value
    assert _analyst().assess_risk(state) is None
    assert not state["risk_assessment"]
    assert not _events(state, "risk_assessment")


def test_完整度按当前清单重算():
    """
    闸门的判据必须与被评分的清单同源。若沿用 state 里过期的 completeness，
    会出现"按旧核实率放行、按新清单打分"的错配。
    """
    state = _dd_state(status_map={"litigation": "unverified",
                                  "enforcement": "unverified",
                                  "dishonesty": "unverified"})
    state["completeness"] = {"required_total": 15, "required_verified": 15,
                             "verified_rate": 1.0, "by_category": {}}   # 人为造一份过期统计
    r = _analyst().assess_risk(state)
    assert r["completeness"]["verified_rate"] < 1.0, "必须用当前清单重算"
    assert state["completeness"]["verified_rate"] == r["completeness"]["verified_rate"]
    assert any("司法" in g for g in r["gates_applied"])


# ---------- ⭐ 评级必须真的进入报告 ----------

_SECTION_8 = {"id": "sec_8", "title": "风险汇总与授信建议", "description": "",
              "section_type": "qualitative", "status": "pending"}


def test_sec_8提示词带评级与闸门():
    state = _dd_state()
    _analyst().assess_risk(state)
    captured = []
    w = _writer(json.dumps({"content": "正文", "key_points": []}), captured)
    asyncio.run(w._write_section(state, dict(_SECTION_8)))

    assert captured, "前提：应当调用了 LLM"
    prompt = captured[0]
    assert state["risk_assessment"]["level"] in prompt, "撰写环节必须看到等级"
    for gate in state["risk_assessment"]["gates_applied"]:
        assert gate in prompt, "闸门必须逐条给到模型，只给分数会被稀释效应误导"


def test_其他章节不注入评级():
    """评级只属于风险汇总章节，注进每一章会诱导模型在各处重复下结论"""
    state = _dd_state()
    _analyst().assess_risk(state)
    captured = []
    w = _writer(json.dumps({"content": "正文", "key_points": []}), captured)
    asyncio.run(w._write_section(state, {"id": "sec_4", "title": "财务分析",
                                         "description": "", "status": "pending"}))
    assert "不涉及风险评级" in captured[0]


def test_模型未产出内容时评级仍进草稿():
    """章节撰写失败也不能让第 8 章既没正文也没等级"""
    state = _dd_state()
    _analyst().assess_risk(state)
    w = _writer("这不是 JSON")
    asyncio.run(w._write_section(state, dict(_SECTION_8)))

    draft = state["draft_sections"].get("sec_8", "")
    assert RISK_BLOCK_MARKER in draft
    assert state["risk_assessment"]["level"] in draft


def test_整合时模型丢弃评级由代码补回():
    """
    提示词要求原样保留评级块，但"不要改写"靠提示词不可靠，必须代码兜底。
    这里模拟模型整合后把评级抹掉的情形。
    """
    state = _dd_state()
    _analyst().assess_risk(state)
    state["outline"] = [dict(_SECTION_8)]
    state["draft_sections"]["sec_8"] = "（草稿）"
    w = _writer(json.dumps({
        "full_report": "## 8 风险汇总与授信建议\n\n综合来看，该企业经营情况总体可控。",
        "executive_summary": "", "conclusions": [], "references": []
    }))
    asyncio.run(w._synthesize_report(state))

    assert RISK_BLOCK_MARKER in state["final_report"], "评级被丢弃时必须由代码补回"
    assert state["risk_assessment"]["level"] in state["final_report"]


def test_修订后评级仍在报告中():
    state = _dd_state()
    _analyst().assess_risk(state)
    state["phase"] = ResearchPhase.REVISING.value
    w = _writer(json.dumps({"revised_content": "修订后的报告正文，未提及等级。",
                            "changes_made": [], "addressed_issues": []}))
    asyncio.run(w.process(state))
    assert RISK_BLOCK_MARKER in state["final_report"]


def test_评级块同时呈现等级与闸门():
    """
    composite_score 不可单独使用（加权平均有稀释效应）：
    本用例的企业综合分落在中风险区间，等级却是高风险及以上——
    渲染若只写分数，读者会得出相反结论。
    """
    state = _dd_state()
    r = _analyst().assess_risk(state)
    block = risk_scorecard.render_markdown(r)
    assert 25 < r["composite_score"] <= 50, f"前提：分数落在中风险区间（实际 {r['composite_score']}）"
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险")
    assert r["level"] in block
    assert any(g in block for g in r["gates_applied"]), "闸门必须与等级同时呈现"
    assert "不可单独使用" in block


def test_评级块不触发扫描器误报():
    """
    评级块会随报告进入 Critic 的扫描范围。渲染文本里若出现
    「未发现被执行记录」而该字段并非 verified，就是我们用自己的输出
    制造了一条 critical —— 每一轮都会被门控降级，且原因极难定位。

    用 5 家评测企业做回归（含司法源故障、多源冲突、主体存疑等状态组合）。
    """
    path = os.path.join(os.path.dirname(__file__), "..", "app", "data", "companies_eval.json")
    with open(path, encoding="utf-8") as f:
        companies = json.load(f)["companies"]
    assert companies, "前提：评测档案不能为空"

    for c in companies:
        checks = build_field_checks(checked_at="2026-08-11T00:00:00")
        facts = profile_to_facts(c)
        fill_field_checks(c, facts, checks)
        block = risk_scorecard.render_markdown(
            risk_scorecard.score(c, checks, compute_completeness(checks))
        )
        found = scan_report(checks, block)
        assert not found, (
            f"{c['company_id']} 的评级块被扫描器判为违规："
            f"{found[0]['field_name']} 命中「{found[0]['matched_claim']}」于「{found[0]['sentence'][:40]}」"
        )


def test_模型照抄评级块后不得出现两份():
    """
    ⚠️ 这是**预期路径而非边界情况**：提示词明确要求模型
    「整块内容必须原样保留」，模型照抄正是它守规矩的表现。

    此时 _pin_risk_block 若无条件前置，报告会出现两个评级块；
    若模型顺手改了措辞，就成了"正确等级 + 被改写的等级"并列——
    恰恰是提示词自己警告的「不得出现两个不同的等级」。

    正确行为：替换，而非追加。
    """
    state = _dd_state()
    DataAnalyst.assess_risk(_analyst(), state)
    block = risk_scorecard.render_markdown(state["risk_assessment"])
    state["draft_sections"]["sec_8"] = block + "\n\n本章分析：该企业存在重大风险。"
    section = {"id": "sec_8", "title": "风险汇总与授信建议"}

    _writer("{}")._pin_risk_block(state, section)

    draft = state["draft_sections"]["sec_8"]
    assert draft.count(RISK_BLOCK_MARKER) == 1,         f"评级块出现 {draft.count(RISK_BLOCK_MARKER)} 次，报告不得含两份评级"
    assert "本章分析" in draft, "替换评级块时不应连正文一起丢掉"


def test_pin_risk_block_幂等():
    """重复调用（如修订后重写章节）不得累积评级块"""
    state = _dd_state()
    DataAnalyst.assess_risk(_analyst(), state)
    state["draft_sections"]["sec_8"] = "正文"
    section = {"id": "sec_8", "title": "风险汇总与授信建议"}
    w = _writer("{}")
    w._pin_risk_block(state, section)
    w._pin_risk_block(state, section)
    assert state["draft_sections"]["sec_8"].count(RISK_BLOCK_MARKER) == 1


def test_ensure_risk_block_幂等():
    """整合与修订各调一次，正文不得出现两份评级"""
    state = _dd_state()
    DataAnalyst.assess_risk(_analyst(), state)
    state["final_report"] = "报告正文"
    w = _writer("{}")
    w._ensure_risk_block(state)
    w._ensure_risk_block(state)
    assert state["final_report"].count(RISK_BLOCK_MARKER) == 1


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
