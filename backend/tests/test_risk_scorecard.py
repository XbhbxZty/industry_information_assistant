"""
风险评分卡与完整度闸门测试

核心断言：**查不到 ≠ 没问题**。

尽调系统最危险的失效模式是：某数据源故障 → 对应字段全部 unverified
→ 该维度无扣分 → 综合分很低 → 输出"低风险，建议授信"。
闸门必须在综合评分**之后**强制施加，且不可被评分覆盖。

运行：cd backend && python tests/test_risk_scorecard.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.risk_scorecard import INSUFFICIENT, LEVELS, score  # noqa: E402


def _checks(status_map):
    """按 field_id -> status 构造清单，未指定者为 verified"""
    cs = build_field_checks()
    for c in cs:
        c["status"] = status_map.get(c["field_id"], "verified")
        if c["status"] == "verified":
            c["value"] = "经查询，无相关记录"
    return cs


def _run(company, status_map=None):
    cs = _checks(status_map or {})
    return score(company, cs, compute_completeness(cs))


_CLEAN = {
    "registration": {"operating_status": "存续"},
    "financials": [{"period": "2025年度", "revenue": 10000.0, "net_profit": 1200.0,
                    "debt_ratio": 0.40, "operating_cash_flow": 900.0}],
    "judicial_records": [], "negative_news": [], "guarantee": [],
    "bidding_records": [{"project": "x", "amount": 100.0, "win_date": "2025-01-01"}],
}


# ---------- ⭐ 核心：查不到 ≠ 没问题 ----------

def test_司法源故障不得判低风险():
    """
    最危险的失效模式：司法三项全未核实，财务健康 → 综合分接近 0。
    没有闸门就会输出"低风险，建议授信"。
    """
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    assert r["composite_score"] < 25, "前提：分数本身很低"
    assert r["level"] != "低风险", "司法维度缺失时绝不能判低风险"
    assert any("司法" in g for g in r["gates_applied"])
    assert r["requires_human_review"] is True


def test_核实率过低时拒绝评级():
    unv = {c["field_id"]: "unverified" for c in build_field_checks()
           if c["field_id"] not in ("registration", "business_scope")}
    r = _run(_CLEAN, unv)
    assert r["level"] == INSUFFICIENT
    assert "不得出具授信建议" in r["credit_advice"] or "补齐" in r["credit_advice"]


def test_未核实字段不得贡献无风险信号():
    """未核实的司法字段不应被当作『无记录』计 0 分"""
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    jud_rules = [x for x in r["triggered_rules"] if x["dimension"] == "judicial"]
    assert not jud_rules, "未核实的司法项不应产生任何评分规则"


# ---------- 一票否决 ----------

def test_失信记录至少高风险():
    c = dict(_CLEAN, judicial_records=[{"type": "失信", "amount": 100}])
    r = _run(c)
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险")
    assert any("失信" in g for g in r["gates_applied"])


def test_被执行记录至少高风险():
    c = dict(_CLEAN, judicial_records=[{"type": "被执行", "amount": 500}])
    r = _run(c)
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险")


def test_作为原告的涉诉不构成负面():
    c = dict(_CLEAN, judicial_records=[
        {"type": "涉诉", "role": "原告", "amount": 200}])
    r = _run(c)
    assert LEVELS.index(r["level"]) < LEVELS.index("高风险"), "原告身份不应触发一票否决"


# ---------- 冲突 ----------

def test_冲突必查项上调一级且强制复核():
    cs = _checks({})
    for c in cs:
        if c["field_id"] == "registration":
            c["status"] = "conflicting"
    base = score(_CLEAN, _checks({}), compute_completeness(_checks({})))
    r = score(_CLEAN, cs, compute_completeness(cs))
    assert LEVELS.index(r["level"]) > LEVELS.index(base["level"]) or r["level"] == "拒绝"
    assert r["requires_human_review"] is True
    assert any("冲突" in g for g in r["gates_applied"])


# ---------- 闸门不可被评分覆盖 ----------

def test_闸门在评分之后施加():
    """即便综合分为 0，闸门仍须生效——这是"不可被评分覆盖"的含义"""
    r = _run(_CLEAN, {"dishonesty": "unverified"})
    assert r["composite_score"] <= 25
    assert r["level"] != "低风险"


def test_综合分会被表现好的维度稀释_故闸门是必需的():
    """
    ⚠️ 这条测试记录的是评分卡的一个**固有性质**，不是缺陷：

    一家已被列为失信被执行人、资产负债率 89.1%、连续亏损、有严重负面舆情的企业，
    综合分只有约 47.5——因为 relation（无对外担保）与 operation（存续）
    这两个"表现好"的维度按权重把分数拉了下来：

        financial  70.0 × 0.30 = 21.0
        judicial   66.7 × 0.30 = 20.0
        relation    0.0 × 0.20 =  0.0   ← 稀释
        operation  15.0 × 0.10 =  1.5
        opinion    50.0 × 0.10 =  5.0
                                 47.5   → 单看分数是"中风险"

    这是加权平均的固有行为。**因此闸门不是锦上添花，而是必需品**——
    真正把这家企业挡住的是"失信 → 至少高风险"这条硬规则，不是分数。

    推论：**综合分不可单独使用**，任何只看分数的下游逻辑都会误判。
    """
    c = {
        "registration": {"operating_status": "存续"},
        "financials": [{"period": "2025年度", "revenue": 9800.0, "net_profit": -2150.0,
                        "debt_ratio": 0.891, "operating_cash_flow": -2870.0}],
        "judicial_records": [{"type": "失信", "amount": 1200},
                             {"type": "被执行", "amount": 1200},
                             {"type": "被执行", "amount": 486}],
        "negative_news": [{"severity": "严重", "subject_confirmed": True}],
        "guarantee": [], "bidding_records": [],
    }
    r = _run(c)
    # 分数本身处在"中风险"区间——这正是问题所在
    assert 25 < r["composite_score"] <= 50, f"实际 {r['composite_score']}"
    # 但闸门把等级顶到了高风险及以上
    assert LEVELS.index(r["level"]) >= LEVELS.index("高风险"), \
        "闸门必须弥补加权平均的稀释效应"
    assert any("失信" in g for g in r["gates_applied"])


# ---------- 可解释性 ----------

def test_每条结论都有规则与证据():
    r = _run(_CLEAN)
    assert r["triggered_rules"], "必须能解释分数由哪些规则构成"
    for rule in r["triggered_rules"]:
        assert rule["detail"], "每条规则须有可读说明"
        assert "field_id" in rule, "须能追溯到具体核查项"


def test_闸门留痕():
    r = _run(_CLEAN, {"litigation": "unverified", "enforcement": "unverified",
                      "dishonesty": "unverified"})
    assert r["gates_applied"], "闸门触发必须留痕，否则无法解释等级为何被提升"


def test_评分可复现():
    """纯函数：同一输入必须给出完全相同的结果（对比 LLM 判定的方差）"""
    a = _run(_CLEAN, {"litigation": "unverified"})
    b = _run(_CLEAN, {"litigation": "unverified"})
    assert a["composite_score"] == b["composite_score"]
    assert a["level"] == b["level"]
    assert a["gates_applied"] == b["gates_applied"]


# ---------- ⚠️ 已知设计问题（显式记录，不靠调阈值掩盖）----------

def test_已知问题_低风险等级当前不可达():
    """
    guarantee_circle 是必查项，但当前**没有任何数据源**
    （需关联图谱推导，属 v0.6 工作），因此永远 unverified。
    relation 维度最高只能到 2/3=67%… 实际当前仅 1/3=33% < 50% 阈值，
    导致该维度恒被排除且等级下限恒被提升至中风险。

    后果：**「低风险」在当前实现下不可达。**

    一个永远达不到的等级说明规则有问题。可选解法：
      (a) 在关联图谱能力就绪前，把 guarantee_circle 降为选查项
      (b) 维度核实率的分母只计"有数据源可查"的项
    本测试锁定当前行为，待 v0.6 决策后再改。
    """
    r = _run(_CLEAN)   # 全部 verified 的理想情况
    assert r["level"] == "低风险", (
        "全部字段 verified 时应可达低风险；若此断言失败，"
        "说明闸门在理想数据下仍被触发，需检查阈值设计"
    )


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
            print(f"  FAIL  {name}: {str(e)[:100]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
