"""
授信额度建议测试（v0.7-A）

## 这一轮在钉什么

`credit_advice` 此前是每个等级一句固定话术：「可考虑授信，建议追加增信措施」。
信贷评审会拿到这句话什么也决定不了——他们要的是**建议多少钱、附什么条件**。

三条设计原则各有断言：

1. **只用已核实的财务数据** —— 未核实的数字不得决定放多少钱
2. **多口径取最小** —— 授信场景里保守是唯一安全的方向
3. **算不出就不出具** —— 给一个有数字的建议，读者就会当它是测算结论

运行：cd backend && python tests/test_credit_advice.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, profile_to_facts, replay_from_profile,
)
from service.credit_advice import (  # noqa: E402
    CASH_FLOW_MULTIPLE, LEVEL_FACTOR, REVENUE_RATIO, recommend_credit,
    render_markdown as render_rec,
)
from service.datasource import apply_all  # noqa: E402
from service.risk_scorecard import (  # noqa: E402
    INSUFFICIENT, PROFILE_BACKED_FIELDS, render_markdown, score, unratable,
)
from service.verification import build_scoring_view  # noqa: E402

_EVAL = os.path.join(os.path.dirname(__file__), "..", "app", "data", "companies_eval.json")


def _companies():
    with open(_EVAL, encoding="utf-8") as f:
        return {c["company_id"]: c for c in json.load(f)["companies"]}


def _pipeline(company):
    """走与生产同一条链路，返回合并后的评分视图与评级"""
    checks = build_field_checks(checked_at="2026-08-13T00:00:00")
    fill_field_checks(company, profile_to_facts(company), checks)
    store = {}
    apply_all(company, checks, store)
    comp = compute_completeness(checks)
    view, _ = build_scoring_view(
        company, checks, store, profile_backed_fields=PROFILE_BACKED_FIELDS,
        profile_replay_fn=replay_from_profile)
    return view, checks, score(view, checks, comp)


def _checks(status_map=None):
    cs = build_field_checks()
    for c in cs:
        c["status"] = (status_map or {}).get(c["field_id"], "verified")
        if c["status"] == "verified":
            c["value"] = "x"
    return cs


def _assessment(level="低风险", rate=1.0, review=False, conflicts=(), unverified=()):
    return {"level": level, "requires_human_review": review,
            "completeness": {"verified_rate": rate,
                             "conflicting_fields": list(conflicts),
                             "unverified_fields": list(unverified)}}


_HEALTHY = {
    "financials": [{"period": "2025年度", "revenue": 10000.0, "net_profit": 800.0,
                    "debt_ratio": 0.40, "operating_cash_flow": 1200.0,
                    "total_assets": 8000.0, "total_liabilities": 3000.0}],
}


# ------------------------------------------------- 原则 1：只用已核实的数据

def test_未核实的财务字段不得参与额度测算():
    """
    ⭐ 用未经核实的数字决定放多少钱，是整套核实架构在授信环节的失守。
    """
    full = recommend_credit(_HEALTHY, _checks(), _assessment())
    assert full["recommendable"]
    methods = {b["method"] for b in full["basis"]}
    assert methods == {"营收法", "净资产法", "现金流法"}

    partial = recommend_credit(
        _HEALTHY, _checks({"revenue": "unverified", "cash_flow": "unverified"}),
        _assessment())
    assert {b["method"] for b in partial["basis"]} == {"净资产法"}, \
        "未核实的营收与现金流不得出现在测算口径里"


def test_财务数据全部未核实时不出具而非给零():
    """算不出 ≠ 可以给个小额度（与 unratable 同一原则）"""
    r = recommend_credit(_HEALTHY, _checks(
        {"revenue": "unverified", "cash_flow": "unverified",
         "debt_ratio": "unverified"}), _assessment())
    assert not r["recommendable"]
    assert r["suggested_amount"] is None
    assert "未核实的数字不得用于决定放款金额" in r["reason"]


# ------------------------------------------------- 原则 2：多口径取最小

def test_多口径取最小值():
    """取最大或取平均都会让某个乐观口径主导结论"""
    r = recommend_credit(_HEALTHY, _checks(), _assessment("低风险", rate=1.0))
    values = [b["value"] for b in r["basis"]]
    # 营收 10000×0.2=2000；净资产 5000×0.8=4000；现金流 1200×3=3600
    assert min(values) == 2000.0
    assert r["suggested_amount"] == 2000.0, f"应取最小口径，实际 {r['suggested_amount']}"
    assert any("取最小值" in a["detail"] for a in r["adjustments"])


def test_现金流为负时该口径归零且留痕():
    """现金流为负不是"这个口径算不出"，而是一个明确的负面信号"""
    neg = {"financials": [dict(_HEALTHY["financials"][0], operating_cash_flow=-500.0)]}
    r = recommend_credit(neg, _checks(), _assessment())
    cf = next(b for b in r["basis"] if b["method"] == "现金流法")
    assert cf["value"] == 0.0
    assert "为负" in cf["detail"]
    assert not r["recommendable"], "取最小值后为零，不得出具额度"


# ------------------------------------------------- 原则 3：算不出就不出具

def test_不予评级时不出具():
    r = recommend_credit(_HEALTHY, _checks(), _assessment(INSUFFICIENT))
    assert not r["recommendable"] and r["suggested_amount"] is None


def test_拒绝等级不出具():
    r = recommend_credit(_HEALTHY, _checks(), _assessment("拒绝"))
    assert not r["recommendable"]
    assert "拒绝" in r["reason"]


def test_不出具时仍给出放款条件():
    """不给额度不等于不给指引：复核人需要知道要补什么"""
    r = recommend_credit(_HEALTHY, _checks(), _assessment(
        INSUFFICIENT, unverified=["litigation"]))
    assert r["conditions"], "不出具额度时仍应给出补齐方向"


def test_unratable自带明确的不出具建议():
    """下游读到 None 会自行脑补一个默认值"""
    rec = unratable("测试", {})["credit_recommendation"]
    assert rec["recommendable"] is False
    assert rec["advice_text"]


# ------------------------------------------------- 调整因子

def test_风险等级系数逐级收紧():
    amounts = {}
    for lvl in ("低风险", "中风险", "高风险"):
        r = recommend_credit(_HEALTHY, _checks(), _assessment(lvl))
        amounts[lvl] = r["suggested_amount"]
    assert amounts["低风险"] > amounts["中风险"] > amounts["高风险"] > 0
    assert amounts["中风险"] == 2000.0 * LEVEL_FACTOR["中风险"]


def test_核实率不足时按比例折减():
    full = recommend_credit(_HEALTHY, _checks(), _assessment(rate=1.0))
    part = recommend_credit(_HEALTHY, _checks(), _assessment(rate=0.70))
    assert part["suggested_amount"] < full["suggested_amount"]
    assert any("核实率折扣" in a["factor"] for a in part["adjustments"])


def test_对外担保作为或有负债全额扣减():
    """被担保方违约时担保人要代偿，这是真实的风控扣减"""
    with_g = dict(_HEALTHY, guarantee=[
        {"beneficiary": "甲", "amount": 500.0, "unit": "万元"}])
    base = recommend_credit(_HEALTHY, _checks(), _assessment())
    ded = recommend_credit(with_g, _checks(), _assessment())
    assert ded["suggested_amount"] == base["suggested_amount"] - 500.0
    assert ded["deductions"][0]["amount"] == 500.0


def test_每个数字都能追溯到依据():
    """信贷评审会要问"这个数怎么来的"，答案不能是"系统算的" """
    r = recommend_credit(_HEALTHY, _checks(), _assessment("中风险", rate=0.8))
    assert r["basis"] and all(b["detail"] for b in r["basis"])
    assert r["adjustments"] and all(a["detail"] for a in r["adjustments"])


def test_测算可复现():
    a = recommend_credit(_HEALTHY, _checks(), _assessment("中风险", rate=0.8))
    b = recommend_credit(_HEALTHY, _checks(), _assessment("中风险", rate=0.8))
    assert a == b


# ------------------------------------------------- 约束项必须如实点名

def test_归零时点名真正的约束项():
    """
    ⚠️ 初版这里统一写"扣减或有负债后授信能力为零"——但多数情况下额度在扣减
    之前就已归零（现金流法为负会把取最小值的结果直接压到 0），担保扣减
    根本不是成因。**解释与真实成因不符**，正是这个项目一直在防的东西。
    """
    neg = {"financials": [dict(_HEALTHY["financials"][0], operating_cash_flow=-500.0)],
           "guarantee": [{"beneficiary": "甲", "amount": 100.0}]}
    r = recommend_credit(neg, _checks(), _assessment())
    assert "现金流法" in r["reason"], f"应点名现金流法，实际：{r['reason']}"
    assert "或有负债" not in r["reason"], "担保扣减不是本例的成因，不得甩锅给它"

    # 反例：确实由担保扣减造成的归零，则必须点名担保
    big_g = dict(_HEALTHY, guarantee=[{"beneficiary": "甲", "amount": 9999.0}])
    r2 = recommend_credit(big_g, _checks(), _assessment())
    assert "担保" in r2["reason"] or "或有负债" in r2["reason"]


# ------------------------------------------------- 申请额度对比

def test_申请超出建议上限时明确提示降额():
    c = dict(_HEALTHY, credit_application={"amount": 5000.0, "unit": "万元"})
    r = recommend_credit(c, _checks(), _assessment())
    assert r["application_amount"] == 5000.0
    assert r["application_gap"] > 0
    assert "降额" in r["advice_text"]


def test_无授信申请时不虚构对比():
    r = recommend_credit(_HEALTHY, _checks(), _assessment())
    assert r["application_amount"] is None
    assert r["application_gap"] is None
    assert "申请" not in r["advice_text"]


# ------------------------------------------------- 增信条件对应真实风险点

def test_条件逐条对应已查实的风险点():
    c = dict(_HEALTHY,
             guarantee=[{"beneficiary": "甲", "amount": 300.0,
                         "board_resolution": "未提供股东会决议"}],
             guarantee_circle=[{"kind": "互保", "path": ["甲", "乙"]}])
    c["financials"] = [dict(_HEALTHY["financials"][0],
                            operating_cash_flow=-100.0, debt_ratio=0.78)]
    conds = recommend_credit(c, _checks(), _assessment(
        "高风险", review=True, conflicts=["registration"]))["conditions"]
    joined = " ".join(conds)
    assert "或有负债" in joined
    assert "股东会决议" in joined
    assert "担保圈" in joined
    assert "受托支付" in joined
    assert "资产负债率" in joined
    assert "冲突" in joined
    assert "未经复核不得放款" in joined


def test_无风险点时不堆砌套话():
    conds = recommend_credit(_HEALTHY, _checks(), _assessment())["conditions"]
    assert conds == [], f"没有查实的风险点就不该有条件：{conds}"


# ------------------------------------------------- 报告呈现

def test_额度建议渲染进评级块():
    c, checks, r = _pipeline(_companies()["EVAL-001"])
    r["credit_recommendation"] = recommend_credit(c, checks, r)
    block = render_markdown(r)
    assert "授信额度建议（规则测算，非模型判断）" in block
    assert "建议区间" in block
    assert "测算口径" in block


def test_不出具时报告写明理由而非留空():
    c, checks, r = _pipeline(_companies()["EVAL-002"])   # 拒绝
    r["credit_recommendation"] = recommend_credit(c, checks, r)
    block = render_markdown(r)
    assert "不出具额度建议" in block
    assert "放款条件与增信要求" in block, "不出具额度时仍应给出条件"


def test_五家评测企业的额度建议方向合理():
    expect = {
        "EVAL-001": True,    # 优质低风险 → 应有额度
        "EVAL-002": False,   # 拒绝 → 不出具
        "EVAL-003": True,    # 中风险 → 有额度但收紧
        "EVAL-004": False,   # 数据不足 → 不出具
        "EVAL-005": False,   # 高风险 + 现金流为负 → 不出具
    }
    amounts = {}
    for cid, company in _companies().items():
        view, checks, r = _pipeline(company)
        rec = recommend_credit(view, checks, r)
        assert rec["recommendable"] is expect[cid], \
            f"{cid} 期望 recommendable={expect[cid]}，实际 {rec['recommendable']}：{rec['reason']}"
        if rec["recommendable"]:
            amounts[cid] = rec["suggested_amount"]
    assert amounts["EVAL-001"] > amounts["EVAL-003"], \
        "优质企业的建议额度应高于中风险企业"


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
            print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
