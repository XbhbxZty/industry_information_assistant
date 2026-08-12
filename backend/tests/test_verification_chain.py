"""
核实来源与结构化证据链测试（v0.6a）

## 这一轮要钉住的东西

`verified_profile_mismatches()` 此前假设**所有** verified 字段都能由初始
`company_profile` 重放。当前成立，只因 Scout 至今只追加 `attempted_sources`。
一旦结构化适配器查到新字段并置 verified，初始档案无法重放，合法增量证据
会被误判为不一致 → 全面 fail-closed。

本轮不让任何东西真正联网核实，只建立来源模型与重放边界，并用断言锁住
四条不可让步的规则：

1. attempted_sources 不是证据
2. 通用网页检索不是结构化核实来源（来源与适配器都是闭集）
3. 来源不明时不得静默猜测，且不得继续形成自动授信等级
4. 通过校验 ≠ 进入评分——证据必须能并进评分卡真正读的那份数据

## 只读复核推翻的三个断言（首版曾全绿）

首版 22 例全通过，但复核发现它们只断言了 `verify_field_checks().ok`，
没有一条走到最终评级。三个洞因此全部漏过：

- 适配器写入「存在1800万元担保」→ 校验通过 → 评分卡照旧输出「未发现对外担保」（BC-31）
- 证据不绑字段/适配器/时间，跨字段借证与网页伪装均可通过（BC-32）
- 「写证据与改状态绑定」只写在注释里，函数根本没改状态（BC-34）

**所以本文件的核心用例一律断言到 `DataAnalyst.assess_risk()` 的产出，
而不是中间报告的 ok 位。**

运行：cd backend && python tests/test_verification_chain.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from config.verification_policy import POLICY  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, profile_retrieved_at, profile_to_facts,
    replay_from_profile, verify_field_checks,
)
from service.risk_scorecard import (  # noqa: E402
    INSUFFICIENT, LEVELS, PROFILE_BACKED_FIELDS, apply_provenance_gate,
)
from service.verification import (  # noqa: E402
    ORIGIN_INITIAL_PROFILE, ORIGIN_STRUCTURED_ADAPTER,
    REASON_CONFLICT_DETAIL_MISMATCH, REASON_CONFLICT_SILENTLY_RESOLVED,
    REASON_EVIDENCE_ADAPTER_MISMATCH, REASON_EVIDENCE_FIELD_MISMATCH,
    REASON_EVIDENCE_MISSING_TIMESTAMP, REASON_EVIDENCE_NOT_FOUND,
    REASON_EVIDENCE_NOT_MERGEABLE, REASON_EVIDENCE_VALUE_MISMATCH,
    REASON_INVALID_ORIGIN, REASON_MISSING_CONFLICT_VALUES, REASON_MISSING_ORIGIN,
    REASON_MISSING_TIMESTAMP, REASON_NO_EVIDENCE_IDS, REASON_PROFILE_REPLAY_MISMATCH,
    REASON_TIMESTAMP_MISMATCH, REASON_UNTRUSTED_ADAPTER,
    build_scoring_view, record_structured_evidence, register_adapter,
    unregister_adapter, verify_evidence_chain,
)
from service.deep_research_v2.agents.data_analyst import DataAnalyst  # noqa: E402
from service.deep_research_v2.graph import build_complete_event  # noqa: E402
from service.deep_research_v2.state import ResearchPhase, create_initial_state  # noqa: E402

_TS = "2026-08-11T00:00:00"
_LATER = "2026-08-11T12:00:00"
_SNAPSHOT = "2026-08-09"

# 本轮没有任何真实适配器，注册表初始为空——这本身就是"闭集生效"的证据。
# 测试自己登记两个替身，正是真实适配器上线时必须走的同一道手续。
register_adapter("guarantee_registry", "测试替身：担保登记")
register_adapter("registry_vs_audit", "测试替身：工商 vs 审计交叉核对")
register_adapter("other_adapter", "测试替身：另一个来源")

# 全部 20 项都已查询：核实率足够高，等级不会被总体完整度闸门吃掉，
# 这样"评级是否反映了适配器证据"才是可观测的。
_QUERIED = [
    "registration", "business_scope", "operating_status", "shareholders",
    "actual_controller", "external_investment", "bidding_record", "revenue",
    "net_profit", "debt_ratio", "cash_flow", "litigation", "enforcement",
    "dishonesty", "equity_freeze", "guarantee", "guarantee_circle",
    "related_party", "negative_news", "regulatory_penalty",
]

_COMPANY = {
    "name": "测试科技有限公司",
    "credit_code": "91440000TEST000002",
    # 数据源快照声明的取证时间。缺了它，每一项都会被记为"取证时间不明"降级——
    # 这正是 BC-35 要求的行为：不许拿运行时间冒充取证时间
    "coverage": {"queried": _QUERIED, "retrieved_at": _SNAPSHOT},
    "registration": {
        "registered_capital": "5000万元人民币", "paid_in_capital": "5000万元人民币",
        "established_date": "2015-01-01", "legal_representative": "张三",
        "company_type": "有限责任公司", "operating_status": "存续",
        "business_scope": "技术开发", "data_source": "工商登记信息",
        "retrieved_at": _SNAPSHOT,
    },
    "shareholders": [{"name": "张三", "type": "自然人", "ratio": 1.0}],
    "actual_controller": {"name": "张三", "basis": "直接持股100%"},
    "financials": [{"period": "2025年度", "revenue": 12000.0, "net_profit": 1500.0,
                    "debt_ratio": 0.42, "operating_cash_flow": 900.0}],
    "bidding_records": [{"project": "某项目", "amount": 500.0, "win_date": "2025-06-01"}],
    "judicial_records": [], "negative_news": [], "regulatory_penalty": [],
}


def _filled_checks(company=_COMPANY):
    """走生产路径填充：fill_field_checks 内部会打来源标记"""
    checks = build_field_checks(checked_at=_TS)
    fill_field_checks(company, profile_to_facts(company), checks)
    return checks


def _pick(checks, field_id):
    return next(c for c in checks if c["field_id"] == field_id)


def _reasons(report):
    return {m["reason"] for m in report.mismatches}


def _raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError as e:
        return str(e)
    raise AssertionError("应当抛 ValueError，实际正常返回")


# ---------- 生产路径夹具（断言必须落到最终评级，不是中间报告）----------

def _analyst():
    agent = DataAnalyst("sk-test", "http://localhost:1", "test-model")

    async def _boom(*a, **k):
        raise RuntimeError("LLM 不可用")

    agent.call_llm = _boom
    return agent


def _dd_state(company=_COMPANY, checks=None, evidence_store=None):
    state = create_initial_state("请对测试科技有限公司做贷前尽职调查", "test-verify")
    state["company_name"] = company.get("name", "")
    state["company_profile"] = company
    state["facts"] = profile_to_facts(company)
    state["field_checks"] = checks if checks is not None else _filled_checks(company)
    state["evidence_store"] = evidence_store or {}
    state["completeness"] = compute_completeness(state["field_checks"])
    state["phase"] = ResearchPhase.ANALYZING.value
    return state


def _rule(result, field_id):
    return [r for r in result["triggered_rules"] if r["field_id"] == field_id]


# ================================================================ 来源模型

def test_档案填充自动打上初始档案来源():
    """漏标一次，该清单在重放时就会被当成来源不明的旧检查点"""
    checks = _filled_checks()
    reg = _pick(checks, "registration")
    assert reg["status"] == "verified"
    assert reg["verification_origin"] == ORIGIN_INITIAL_PROFILE
    assert reg["retrieved_at"], "缺时间戳的核实项无法判断证据时效"


def test_取证时间取自档案声明而非运行时刻():
    """
    BC-35：此前用 datetime.now() 当取证时间，记录的是程序读档案的时刻。
    报告会显示"本次核查于今日完成"，而数据可能是三个月前抓的。
    """
    checks = _filled_checks()
    assert _pick(checks, "registration")["retrieved_at"] == _SNAPSHOT
    # 事件型字段查到空、没有记录可取时间时，退到数据源快照声明，同样不是 now()
    assert _pick(checks, "litigation")["retrieved_at"] == _SNAPSHOT
    assert profile_retrieved_at(_COMPANY)["revenue"] == _SNAPSHOT


def test_档案未声明取证时间时记为降级而不伪造():
    undated = {k: v for k, v in _COMPANY.items() if k != "coverage"}
    undated["coverage"] = {"queried": _QUERIED}          # 没有 retrieved_at
    undated["registration"] = {k: v for k, v in _COMPANY["registration"].items()
                               if k != "retrieved_at"}
    checks = _filled_checks(undated)
    assert _pick(checks, "litigation")["retrieved_at"] == "", "取不到就留空，不得编造"
    report = verify_field_checks(undated, checks, {})
    assert report.ok, "取证时间不明属于降级，不是证据链断裂"
    assert any(d["reason"] == REASON_MISSING_TIMESTAMP for d in report.degradations)


def test_未核实项不打来源标记():
    """unverified 不主张任何事实；给它标来源会让「有来源」失去含义"""
    # 主夹具 20 项全查到，构造不出未核实项——换一份只覆盖工商源的档案
    partial = {k: v for k, v in _COMPANY.items() if k != "coverage"}
    partial["coverage"] = {"queried": ["registration", "business_scope"],
                           "retrieved_at": _SNAPSHOT}
    checks = _filled_checks(partial)
    unv = [c for c in checks if c["status"] == "unverified"]
    assert unv, "前提：应存在未核实项"
    assert all(not c.get("verification_origin") for c in unv)
    assert all(not c.get("retrieved_at") for c in unv), "未核实项也不该有取证时间"


# ================================================== 必测 1-2：初始档案重放

def test_初始档案核实项正常重放():
    checks = _filled_checks()
    report = verify_field_checks(_COMPANY, checks, {})
    assert report.ok, f"合法档案重放不应报错：{report.mismatches}"
    assert not report.degradations, f"声明了取证时间就不该有降级：{report.degradations}"


def test_初始档案字段缺失时fail_closed():
    """清单仍是 verified，但档案里的 registration 已经没了"""
    checks = _filled_checks()
    broken = {k: v for k, v in _COMPANY.items() if k != "registration"}
    report = verify_field_checks(broken, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


def test_档案取值被改动时fail_closed():
    checks = _filled_checks()
    _pick(checks, "business_scope")["value"] = "被篡改的经营范围"
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


# ============================== 必测 3：适配器增量证据不被错杀

def _record_guarantee(store, check, *, amount=1800.0, adapter="guarantee_registry",
                      retrieved_at=_TS):
    """一次原子写入：状态、取值、证据、来源、时间全部由入口写"""
    return record_structured_evidence(
        store, check,
        source_adapter=adapter, status="verified",
        value=f"为关联方提供连带责任保证{amount:.0f}万元",
        profile_patch={"guarantee": [
            {"beneficiary": "关联方甲", "guarantee_type": "连带责任保证",
             "amount": amount, "unit": "万元"}
        ]},
        raw={"api": "mock_guarantee_registry", "hit": 1},
        retrieved_at=retrieved_at,
    )


def test_结构化适配器新增合法证据不被错杀():
    """
    本轮的核心用例：适配器查到了初始档案里没有的字段。
    旧实现会把它误判为"清单说已核实但档案无法复现" → 全面 fail-closed。
    """
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    assert g["value"] == "经查询，无相关记录", "前提：档案侧认为无担保"

    _record_guarantee(store, g)

    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok, f"合法的适配器增量证据不应被判为不一致：{report.mismatches}"


def test_适配器证据与档案并存时各按各的来源重放():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g)
    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok
    assert _pick(checks, "registration")["verification_origin"] == ORIGIN_INITIAL_PROFILE
    assert g["verification_origin"] == ORIGIN_STRUCTURED_ADAPTER


# ============================ 必测 4-5：只改状态不写证据 / 原子写入口

def test_只改状态不写证据时fail_closed():
    """要求 5 的反面：适配器若只改状态不落证据，必须被拦"""
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g.update({
        "status": "verified", "value": "凭空出现的取值",
        "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
        "source_adapter": "guarantee_registry", "retrieved_at": _TS,
    })
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_NO_EVIDENCE_IDS in _reasons(report)


def test_原子入口一次性写完状态与证据():
    """
    BC-34：首版的 record_structured_evidence 只写来源字段，status/value
    仍要调用方自己改——文档却宣称"绑定后那条路径走不通"。
    这条断言就是那句话的验收：调用前不碰任何状态字段。
    """
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    before_status, before_value = g["status"], g["value"]

    ev_id = _record_guarantee(store, g)

    assert (before_status, before_value) != (g["status"], g["value"]), "前提：调用前未手工改状态"
    assert g["status"] == "verified"
    assert g["value"] == "为关联方提供连带责任保证1800万元"
    assert g["verification_origin"] == ORIGIN_STRUCTURED_ADAPTER
    assert g["source_adapter"] == "guarantee_registry"
    assert g["retrieved_at"] == _TS
    assert g["evidence_ids"] == [ev_id]
    assert g["conflict_detail"] == []
    assert verify_field_checks(_COMPANY, checks, store).ok


def test_原子入口拒绝未注册适配器():
    """P0-2：source_adapter 若是自由字符串，web_search 传进来就获得了结构化身份"""
    checks = _filled_checks()
    msg = _raises(record_structured_evidence, {}, _pick(checks, "guarantee"),
                  source_adapter="web_search", status="verified", value="网上说没有担保",
                  raw={"x": 1}, retrieved_at=_TS)
    assert "未注册" in msg


def test_原子入口拒绝非法状态组合():
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    base = dict(source_adapter="guarantee_registry", raw={"x": 1}, retrieved_at=_TS)

    assert "非空 value" in _raises(record_structured_evidence, {}, g,
                                   status="verified", value=None, **base)
    assert "unverified" in _raises(record_structured_evidence, {}, g,
                                   status="unverified", value="x", **base)
    assert "raw 不得为空" in _raises(
        record_structured_evidence, {}, g, status="verified", value="x",
        source_adapter="guarantee_registry", raw={}, retrieved_at=_TS)
    assert "ISO" in _raises(
        record_structured_evidence, {}, g, status="verified", value="x",
        source_adapter="guarantee_registry", raw={"x": 1}, retrieved_at="昨天")


def test_原子入口拒绝同源同值伪造冲突():
    """两条一模一样的记录凑不出分歧；冲突会抬高等级，不能被这么造出来"""
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    base = dict(source_adapter="registry_vs_audit", status="conflicting",
                raw={"x": 1}, retrieved_at=_TS)
    assert "不同来源" in _raises(
        record_structured_evidence, {}, g,
        conflict_values=[{"source": "工商登记", "value": "5000万"},
                         {"source": "工商登记", "value": "5000万"}], **base)
    assert "不同来源" in _raises(
        record_structured_evidence, {}, g,
        conflict_values=[{"source": "工商登记", "value": "5000万"},
                         {"source": "财务附注", "value": "5000万"}], **base)


# ==================================== 必测 6-7：证据存在性与取值一致性

def test_evidence_id不存在时fail_closed():
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g.update({
        "status": "verified", "value": "x",
        "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
        "source_adapter": "guarantee_registry", "retrieved_at": _TS,
        "evidence_ids": ["ev_不存在的ID"],
    })
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_EVIDENCE_NOT_FOUND in _reasons(report)


def test_证据值与清单值不一致时fail_closed():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g)
    g["value"] = "担保800万元"          # 证据落好之后清单取值被改掉

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_EVIDENCE_VALUE_MISMATCH in _reasons(report)


def test_时间戳缺失时fail_closed():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g)
    g["retrieved_at"] = ""             # 时效未知，不予采信

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_MISSING_TIMESTAMP in _reasons(report)


# ============================ P0-2：证据必须绑定字段 / 适配器 / 时间 / 冲突

def test_跨字段借用证据被拦():
    """取值相同不代表说的是同一件事：担保项不能拿关联方的证据当依据"""
    checks = _filled_checks()
    store = {}
    rp = _pick(checks, "related_party")
    ev = record_structured_evidence(
        store, rp, source_adapter="guarantee_registry", status="verified",
        value="同一个取值", profile_patch={"related_party": [{"name": "甲", "relation": "同一实控人"}]},
        raw={"x": 1}, retrieved_at=_TS)

    g = _pick(checks, "guarantee")
    g.update({"status": "verified", "value": "同一个取值",
              "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
              "source_adapter": "guarantee_registry", "retrieved_at": _TS,
              "evidence_ids": [ev]})

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_EVIDENCE_FIELD_MISMATCH in _reasons(report)


def test_适配器与证据来源不一致被拦():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g, adapter="guarantee_registry")
    g["source_adapter"] = "other_adapter"      # 清单改称来自另一个来源

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_EVIDENCE_ADAPTER_MISMATCH in _reasons(report)


def test_证据本体缺时间戳被拦():
    """只查 field_check 上的副本不够——证据本体才是时效的依据"""
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    ev = _record_guarantee(store, g)
    store[ev].pop("retrieved_at")

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_EVIDENCE_MISSING_TIMESTAMP in _reasons(report)


def test_清单时间戳与最新证据不一致被拦():
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g, retrieved_at=_TS)
    g["retrieved_at"] = _LATER                  # 声称比证据更新

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_TIMESTAMP_MISMATCH in _reasons(report)


def test_网页来源伪装成结构化适配器被拦():
    """
    双保险：写入口拒绝未注册适配器（见上），即便有人绕过写入口手工构造，
    重放也必须拦住——注册表校验不能只在写入侧。
    """
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    ev = _record_guarantee(store, g)
    store[ev]["source_adapter"] = "web_search"
    g["source_adapter"] = "web_search"

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_UNTRUSTED_ADAPTER in _reasons(report)


def test_同字段两个不同取值标verified被拦():
    """
    这正是系统一直防范的 conflict_silently_resolved，只是发生在证据层：
    5000万和1500万同时存在，却单方面采信其一标成已核实。
    """
    checks = _filled_checks()
    store = {}
    g = _pick(checks, "guarantee")
    _record_guarantee(store, g, amount=5000.0)
    _record_guarantee(store, g, amount=1500.0)
    g["value"] = "为关联方提供连带责任保证5000万元"

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_CONFLICT_SILENTLY_RESOLVED in _reasons(report)


def test_conflict_detail与证据不一致被拦():
    """报告披露的分歧内容必须就是证据里的那份"""
    checks = _filled_checks()
    store = {}
    reg = _pick(checks, "registration")
    record_structured_evidence(
        store, reg, source_adapter="registry_vs_audit", status="conflicting",
        conflict_values=[{"source": "工商登记", "value": "实缴5000万元"},
                         {"source": "财务附注", "value": "实缴1500万元"}],
        raw={"x": 1}, retrieved_at=_TS)
    reg["conflict_detail"] = [{"source": "工商登记", "value": "实缴5000万元"},
                              {"source": "财务附注", "value": "实缴4900万元"}]

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_CONFLICT_DETAIL_MISMATCH in _reasons(report)


# ================================ 必测 8-9：attempted_sources 与网页事实

def test_attempted_sources不能升级为verified():
    """仅凭尝试记录升级状态，等于把"我搜过"当成"我核实了" """
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g.update({
        "status": "verified", "value": "无对外担保",
        "attempted_sources": ["guarantee_registry", "web_search"],
        "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
        "source_adapter": "guarantee_registry", "retrieved_at": _TS,
    })
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_NO_EVIDENCE_IDS in _reasons(report)
    fail = next(m for m in report.mismatches if m["reason"] == REASON_NO_EVIDENCE_IDS)
    assert fail["attempted_sources"], "失败记录应带上 attempted_sources 便于排查"


def test_通用网页检索不是合法核实来源():
    """合法来源是闭集，web_search / llm_inference 不在其中"""
    for bogus in ("web_search", "scout_web", "llm_inference", "unknown"):
        checks = _filled_checks()
        g = _pick(checks, "guarantee")
        g.update({"status": "verified", "value": "网上说没有担保",
                  "verification_origin": bogus, "source_adapter": bogus,
                  "retrieved_at": _TS, "evidence_ids": []})
        report = verify_field_checks(_COMPANY, checks, {})
        assert not report.ok, bogus
        assert REASON_INVALID_ORIGIN in _reasons(report), bogus


def test_网页事实进入facts不改变任何字段状态():
    """
    Scout 把网页内容写进 facts 是允许的（那是给 LLM 读的素材），
    但 facts 数量增长不得让任何字段变成 verified。

    ⚠️ 首版这条是空测试：只比较同一个 dict 前后是否相等，根本没调生产代码。
       现在走 fill_field_checks——真正决定字段状态的那个函数——喂进带网页
       事实的 facts，断言状态与不带时逐项一致。
    """
    baseline = _filled_checks()
    web_facts = profile_to_facts(_COMPANY) + [
        {"id": "f_web1", "content": "网传该公司无对外担保", "source_name": "某论坛",
         "source_type": "news", "metadata": {"category": "relation"}},
        {"id": "f_web2", "content": "据帖子称该公司实控人另有其人", "source_name": "某贴吧",
         "source_type": "news", "metadata": {"category": "equity"}},
    ]
    with_web = build_field_checks(checked_at=_TS)
    fill_field_checks(_COMPANY, web_facts, with_web)

    before = {c["field_id"]: (c["status"], c["value"]) for c in baseline}
    after = {c["field_id"]: (c["status"], c["value"]) for c in with_web}
    assert before == after, "网页事实不得改变任何字段的状态或取值"
    assert _pick(with_web, "guarantee")["verification_origin"] == ORIGIN_INITIAL_PROFILE
    assert _pick(with_web, "actual_controller")["value"] == "张三（认定依据：直接持股100%）"


# ======================================== 必测 10：conflicting 完整证据链

def test_conflicting同样要求完整证据链():
    """冲突会抬高风险等级并强制人工复核；没有出处的冲突与编造的冲突无法区分"""
    checks = _filled_checks()
    reg = _pick(checks, "registration")
    reg["status"] = "conflicting"
    reg["value"] = None
    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok
    assert REASON_PROFILE_REPLAY_MISMATCH in _reasons(report)


def test_conflicting证据必须保留至少两个来源取值():
    """写入口拦不住手工构造的单条冲突证据，重放侧必须也拦"""
    checks = _filled_checks()
    store = {}
    reg = _pick(checks, "registration")
    record_structured_evidence(
        store, reg, source_adapter="registry_vs_audit", status="conflicting",
        conflict_values=[{"source": "工商登记", "value": "实缴5000万元"},
                         {"source": "财务附注", "value": "实缴1500万元"}],
        raw={"x": 1}, retrieved_at=_TS)
    # 事后把证据削成一条
    ev_id = reg["evidence_ids"][0]
    store[ev_id]["conflict_values"] = [{"source": "工商登记", "value": "实缴5000万元"}]

    report = verify_field_checks(_COMPANY, checks, store)
    assert not report.ok
    assert REASON_MISSING_CONFLICT_VALUES in _reasons(report)


def test_conflicting证据齐备时通过并保留各来源取值():
    checks = _filled_checks()
    store = {}
    reg = _pick(checks, "registration")
    ev_id = record_structured_evidence(
        store, reg, source_adapter="registry_vs_audit", status="conflicting",
        conflict_values=[{"source": "工商登记", "value": "实缴5000万元"},
                         {"source": "财务附注", "value": "实缴1500万元"}],
        raw={"src": "cross_check"}, retrieved_at=_TS)
    report = verify_field_checks(_COMPANY, checks, store)
    assert report.ok, report.mismatches
    stored = store[ev_id]["conflict_values"]
    assert len(stored) == 2, "各来源取值必须原样保留，供复核人判断该信谁"
    assert {c["source"] for c in stored} == {"工商登记", "财务附注"}
    assert reg["value"] is None, "冲突项不得携带单一取值"
    assert {c["source"] for c in reg["conflict_detail"]} == {"工商登记", "财务附注"}


# ============================================ 旧检查点：默认严格 + 显式降级

def test_旧检查点缺来源时默认不予采信():
    """
    BC-33：首版默认 allow_legacy_profile_replay=True，一份全 verified、
    全无来源标记的旧检查点可以一路走到自动评级。默认必须取严。
    """
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
        c.pop("retrieved_at", None)

    report = verify_field_checks(_COMPANY, checks, {})
    assert not report.ok, "来源未迁移的检查点不得进入自动评级路径"
    assert REASON_MISSING_ORIGIN in _reasons(report)
    assert report.degradations, "降级事实同样要记录"


def test_显式开启兜底时不阻断但必然记降级():
    """兜底只留给迁移工具与非决策读取，且降级必须可见"""
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
        c.pop("retrieved_at", None)

    report = verify_field_checks(_COMPANY, checks, {}, allow_legacy_profile_replay=True)
    assert report.ok, "档案能重放出来时不阻断，保持旧检查点可读"
    assert report.degradations
    assert all(d["reason"] == REASON_MISSING_ORIGIN for d in report.degradations)


def test_旧检查点且档案也重放不出时仍然fail_closed():
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
    broken = {k: v for k, v in _COMPANY.items() if k != "registration"}
    report = verify_field_checks(broken, checks, {}, allow_legacy_profile_replay=True)
    assert not report.ok, "降级兜底不等于放行——档案也对不上时必须拦住"


def test_配置项决定默认口径而非函数默认值():
    """开关必须在统一配置里可见，不能藏在签名里（BC-33）"""
    assert POLICY.allow_legacy_profile_replay is False
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
    original = POLICY.allow_legacy_profile_replay
    try:
        POLICY.allow_legacy_profile_replay = True
        assert verify_field_checks(_COMPANY, checks, {}).ok, "配置应当真正生效"
    finally:
        POLICY.allow_legacy_profile_replay = original
    assert not verify_field_checks(_COMPANY, checks, {}).ok


# ============================== ⭐ 生产行为断言：证据必须影响最终评级

def test_适配器写入的担保证据必须进入评级():
    """
    ⭐ 本轮最重要的一条（BC-31）。

    首版只断言到 `verify_field_checks().ok`，于是漏掉了：证据通过校验，
    评分卡却仍从旧档案取值，输出「未发现对外担保」——一条新增的负面证据
    被翻译成了正面结论。断言必须落到 DataAnalyst 的最终产出。
    """
    checks = _filled_checks()
    store = {}
    _record_guarantee(store, _pick(checks, "guarantee"), amount=1800.0)

    state = _dd_state(checks=checks, evidence_store=store)
    result = _analyst().assess_risk(state)

    assert result["level"] != INSUFFICIENT, f"合法证据不应导致不可评级：{result['gates_applied']}"
    rules = _rule(result, "guarantee")
    assert rules, "评分卡必须对 guarantee 出规则"
    assert rules[0]["score"] > 0, f"1800万元担保必须扣分，实际 {rules[0]}"
    assert "1800" in rules[0]["detail"], f"规则说明应体现担保金额：{rules[0]['detail']}"
    assert all("未发现对外担保" not in r["detail"] for r in result["triggered_rules"]), \
        "新增负面证据被翻译成了正面结论"


def test_证据无法并入评分视图时不予评级():
    """
    只有 value 没有 profile_patch 的证据能通过校验，但评分卡读不到它。
    这种情况必须 fail-closed，而不是拿旧档案算出一个干净分数。
    """
    checks = _filled_checks()
    store = {}
    record_structured_evidence(
        store, _pick(checks, "guarantee"), source_adapter="guarantee_registry",
        status="verified", value="为关联方提供连带责任保证1800万元",
        raw={"x": 1}, retrieved_at=_TS)          # 刻意不给 profile_patch

    view, unmergeable = build_scoring_view(
        _COMPANY, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS)
    assert unmergeable and unmergeable[0]["reason"] == REASON_EVIDENCE_NOT_MERGEABLE
    assert not view.get("guarantee"), "前提：证据确实没能进档案"

    state = _dd_state(checks=checks, evidence_store=store)
    result = _analyst().assess_risk(state)
    assert result["level"] == INSUFFICIENT
    assert result["requires_human_review"]
    assert any("profile_patch" in e for e in state["errors"])


def test_来源降级不得自动落到低风险():
    """
    BC-33 的生产行为断言：一份取证时间不明的档案，即便各项都干净，
    也不得自动输出最宽松的结论。
    """
    undated = {k: v for k, v in _COMPANY.items() if k != "coverage"}
    undated["coverage"] = {"queried": _QUERIED}
    undated["registration"] = {k: v for k, v in _COMPANY["registration"].items()
                               if k != "retrieved_at"}

    clean = _analyst().assess_risk(_dd_state(_COMPANY))
    state = _dd_state(undated)
    degraded = _analyst().assess_risk(state)

    assert degraded["level"] != INSUFFICIENT, "降级不等于证据链断裂"
    assert LEVELS.index(degraded["level"]) >= LEVELS.index("中风险"), \
        f"来源降级后不得落到低风险，实际 {degraded['level']}"
    assert LEVELS.index(degraded["level"]) >= LEVELS.index(clean["level"]), \
        "降级只能让结论更保守，不能更宽松"
    assert degraded["requires_human_review"]
    assert any("取证时间不明" in g or "来源" in g for g in degraded["gates_applied"]), \
        f"闸门必须留痕：{degraded['gates_applied']}"
    assert degraded["provenance_degradations"]


def test_旧检查点走生产路径时不予评级():
    """默认严格口径下，来源未迁移的检查点连评级都不该产出"""
    checks = _filled_checks()
    for c in checks:
        c.pop("verification_origin", None)
        c.pop("retrieved_at", None)
    state = _dd_state(checks=checks)
    result = _analyst().assess_risk(state)
    assert result["level"] == INSUFFICIENT
    assert result["requires_human_review"]


def test_降级与证据链结论进入SSE与终局事件():
    """只写进日志或 errors 是不够的：只等最终事件的调用方必须也能看到"""
    undated = {k: v for k, v in _COMPANY.items() if k != "coverage"}
    undated["coverage"] = {"queried": _QUERIED}
    undated["registration"] = {k: v for k, v in _COMPANY["registration"].items()
                               if k != "retrieved_at"}
    state = _dd_state(undated)
    _analyst().assess_risk(state)

    evs = [m for m in state["messages"] if m.get("type") == "risk_assessment"]
    assert len(evs) == 1
    assert evs[0]["content"]["provenance_degradations"], "SSE 必须带降级明细"

    ev = build_complete_event(state, [])
    assert ev["risk_assessment"]["gates_applied"]
    assert ev["errors"], "终局事件必须携带降级/错误，否则只等最终结果就看不到"
    assert any("证据链降级" in e for e in ev["errors"])


def test_适配器证据全链路到终局事件():
    """从原子写入到 research_complete 的完整贯通"""
    checks = _filled_checks()
    store = {}
    _record_guarantee(store, _pick(checks, "guarantee"), amount=2600.0)
    state = _dd_state(checks=checks, evidence_store=store)
    _analyst().assess_risk(state)

    ev = build_complete_event(state, [])
    ra = ev["risk_assessment"]
    assert ra["level"] != INSUFFICIENT
    assert any(r["field_id"] == "guarantee" and r["score"] > 0
               for r in ra["triggered_rules"]), "终局事件里必须看得到担保扣分"
    assert not ev["errors"], f"合法证据不应产生错误：{ev['errors']}"


def test_评分视图合并采用追加而非替换():
    """替换会抹掉档案里已有的负面记录；追加最多重复计数，方向安全"""
    company = dict(_COMPANY)
    company["guarantee"] = [{"beneficiary": "旧记录", "guarantee_type": "保证",
                             "amount": 300.0, "unit": "万元"}]
    checks = _filled_checks(company)
    store = {}
    _record_guarantee(store, _pick(checks, "guarantee"), amount=1800.0)
    view, unmergeable = build_scoring_view(
        company, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS)
    assert not unmergeable
    assert len(view["guarantee"]) == 2, "档案原有记录不得被适配器覆盖"
    assert company["guarantee"] and len(company["guarantee"]) == 1, "不得就地修改入参档案"


# ============================================================ 回归：旧接口

def test_旧接口verified_profile_mismatches仍可用():
    from service.company_profile import verified_profile_mismatches
    checks = _filled_checks()
    assert verified_profile_mismatches(_COMPANY, checks) == []
    _pick(checks, "business_scope")["value"] = "改过的值"
    assert verified_profile_mismatches(_COMPANY, checks), "漂移仍应被检出"


def test_replay_from_profile返回按字段索引的预测():
    checks = _filled_checks()
    predicted = replay_from_profile(_COMPANY, checks)
    assert predicted["registration"]["status"] == "verified"
    assert set(predicted) == {c["field_id"] for c in checks}


def test_未注册适配器可被撤销以隔离测试():
    register_adapter("_tmp_adapter", "临时")
    assert verify_evidence_chain(_COMPANY, [], {}).ok
    unregister_adapter("_tmp_adapter")
    checks = _filled_checks()
    g = _pick(checks, "guarantee")
    g.update({"status": "verified", "value": "x",
              "verification_origin": ORIGIN_STRUCTURED_ADAPTER,
              "source_adapter": "_tmp_adapter", "retrieved_at": _TS,
              "evidence_ids": ["ev_x"]})
    assert REASON_UNTRUSTED_ADAPTER in _reasons(verify_field_checks(_COMPANY, checks, {}))


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
