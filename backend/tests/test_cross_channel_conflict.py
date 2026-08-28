# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
跨通道一致性：只在能正面确立矛盾时才报（BC-68）

## 这一轮在钉什么

原实现是一行字符串比对：

    _compact(row["value"]) != _compact(check["value"])

左边是文档里的一个单元格（`"501,200"`），右边是档案渲染出来的展示字符串
（`"2023年度 41250.0万元；2024年度 46800.0万元；2025年度 50120.0万元"`）。
**这两个永远不可能相等**——与单位归一无关，归一了也还是不可比。

实测一次完整运行：4 个同时被档案和 RAG 覆盖的字段**全部**报"可能不一致"，
而两边说的是同一个数（50120 万元 = 501,200 千元）。**误报率 100%。**

后果不是多几条噪声。这是跨通道一致性检查，本该抓真实的档案-文档矛盾
（档案写注册资本 8000 万、文档写 5000 万那种核心风险线索）。永远为真的告警
会让复核人学会忽略它，**真矛盾出现时一并被忽略**。

## 断言分三层

1. **同值不报**：单位不同但归一后相等 → 不得进 errors
2. **异值要报**：同期间归一后确实不等 → 必须进 errors，且说明里带两侧取值
3. **比不了要如实说比不了**：既不报成一致，也不报成矛盾，单独留痕
   ——"无法判定"被藏起来就是重犯 BC-51

运行：cd backend && python -m pytest tests/test_cross_channel_conflict.py -q
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.rag_evidence_bridge import (  # noqa: E402
    _cross_channel_conflict, _profile_numeric,
)

# 实测语料：档案用万元，年报用千元，说的是同一个数。
COMPANY = {
    "name": "云岭恒晟精密机械有限公司",
    "financials": [
        {"period": "2023年度", "revenue": 41250.0, "net_profit": 2980.0,
         "operating_cash_flow": 2640.0, "debt_ratio": 0.5515, "unit": "万元"},
        {"period": "2024年度", "revenue": 46800.0, "net_profit": 2410.0,
         "operating_cash_flow": 1180.0, "debt_ratio": 0.6019, "unit": "万元"},
        {"period": "2025年度", "revenue": 50120.0, "net_profit": 1560.0,
         "operating_cash_flow": 640.0, "debt_ratio": 0.6328, "unit": "万元"},
    ],
}


def _rag(value, numeric, unit, period, field="revenue"):
    return {"field_id": field, "value": value, "numeric_value": numeric,
            "unit": unit, "period": period, "source": "local://doc#p1"}


# ------------------------------------------------------- 一、同值不得误报

def test_same_value_in_different_units_is_not_a_conflict():
    """本条的直接回归：50120 万元 vs 501,200 千元是同一个数。"""
    rows = [_rag("501,200", "501200", "千元", "2025年度")]
    conflict, undecidable = _cross_channel_conflict("revenue", rows, COMPANY)
    assert conflict is None, f"同值被误报为矛盾：{conflict}"
    assert not undecidable, f"同值也不该记成无法判定：{undecidable}"


def test_the_four_fields_that_all_false_fired_are_now_silent():
    """实测那一轮的四个字段里，三个数值字段现在必须全部安静。

    （registration 是非数值字段，另有用例覆盖。）
    """
    cases = [
        ("revenue", _rag("501,200", "501200", "千元", "2025年度", "revenue")),
        ("cash_flow", _rag("6,400", "6400", "千元", "2025年度", "cash_flow")),
        ("net_profit", _rag("15,600", "15600", "千元", "2025年度", "net_profit")),
    ]
    for field_id, row in cases:
        conflict, _ = _cross_channel_conflict(field_id, [row], COMPANY)
        assert conflict is None, f"{field_id} 仍被误报：{conflict}"


def test_ratio_field_normalizes_percent_against_decimal():
    """档案存小数 0.6328，年报写 63.28%——同一个比率。"""
    rows = [{"field_id": "debt_ratio", "value": "63.28%", "numeric_value": "63.28",
             "unit": "%", "period": "2025年度", "source": "s"}]
    conflict, undecidable = _cross_channel_conflict("debt_ratio", rows, COMPANY)
    assert conflict is None, f"比率口径被误报：{conflict}"
    assert not undecidable


# --------------------------------------------------------- 二、异值必须报

def test_genuinely_different_value_is_reported_with_both_sides():
    """真矛盾是核心风险线索，必须报，且要能看到两边各是多少。"""
    rows = [_rag("480,000", "480000", "千元", "2025年度")]
    conflict, undecidable = _cross_channel_conflict("revenue", rows, COMPANY)
    assert conflict, "档案 50120 万元 vs 文档 48000 万元，必须报矛盾"
    assert "2025年度" in conflict
    assert "50120" in conflict and "48000" in conflict, \
        f"说明里必须带两侧取值，复核人才知道去核对什么：{conflict}"
    assert not undecidable


def test_conflict_wins_over_undecidable_when_both_present():
    """一条比不了、一条确实矛盾 → 必须报矛盾，不能被"无法判定"吃掉。"""
    rows = [
        _rag("9,999", "9999", "千元", "期末"),          # 期间对不上，比不了
        _rag("480,000", "480000", "千元", "2025年度"),  # 确实矛盾
    ]
    conflict, _ = _cross_channel_conflict("revenue", rows, COMPANY)
    assert conflict, "存在可确立的矛盾时必须报"


# ------------------------------------------ 三、比不了要如实说比不了

def test_non_numeric_field_is_undecidable_not_conflicting():
    """非数值字段：档案存展示字符串、RAG 存单元格，两者不可比。

    报成矛盾就是误报率 100% 的老路；报成一致则是替复核人下了没依据的结论。
    """
    rows = [{"field_id": "registration", "value": "91990099MA9XFICT02",
             "source": "s", "period": ""}]
    conflict, undecidable = _cross_channel_conflict("registration", rows, COMPANY)
    assert conflict is None, "不可比的字段不得报矛盾"
    assert undecidable, "但必须如实记下「不可比」，藏起来就是重犯 BC-51"


def test_period_absent_from_profile_is_undecidable():
    """文档给了 2022 年度，档案里没有这一期——比不了，不是矛盾。"""
    rows = [_rag("380,000", "380000", "千元", "2022年度")]
    conflict, undecidable = _cross_channel_conflict("revenue", rows, COMPANY)
    assert conflict is None
    assert "2022年度" in undecidable


def test_candidate_without_numeric_is_undecidable():
    rows = [{"field_id": "revenue", "value": "约五亿元", "numeric_value": "",
             "unit": "", "period": "2025年度", "source": "s"}]
    conflict, undecidable = _cross_channel_conflict("revenue", rows, COMPANY)
    assert conflict is None
    assert "数值" in undecidable


# --------------------------------------------------- 四、档案取值归一

def test_profile_numeric_normalizes_to_canonical_unit():
    assert _profile_numeric("revenue", COMPANY, "2025年度") == Decimal("50120")
    assert _profile_numeric("cash_flow", COMPANY, "2025年度") == Decimal("640")
    assert _profile_numeric("debt_ratio", COMPANY, "2025年度") == Decimal("0.6328")
    assert _profile_numeric("revenue", COMPANY, "2019年度") is None
    assert _profile_numeric("inventory", COMPANY, "2025年度") is None, \
        "非核心财务字段没有档案载体，必须返回 None 而不是猜"


def test_no_second_string_comparison_survives_in_the_conflict_path():
    """结构性守卫：本条的成因是一行朴素字符串比对，不得复活。

    ⚠️ 判据必须**剥掉 docstring** 再扫。该函数的 docstring 刻意引用了旧的错误
    写法作为反例——那是文档的价值所在，不是违规。第一版没剥，当场把自己的
    说明判成了违规：**这是本轮第三次写出误伤文档的结构性守卫**
    （前两次：把"消融装置见 eval/retrieval_fixture.py"判成生产依赖、
    把 UI 展示的 `results[:5]` 判成抽取截断）。判据没校准就会把正确的东西
    判成错的，并推着人去改它——BC-59/BC-60 一族。
    """
    import ast
    import inspect

    from service import rag_evidence_bridge as bridge

    tree = ast.parse(inspect.getsource(bridge._cross_channel_conflict).lstrip())
    func = tree.body[0]
    if (func.body and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)):
        func.body = func.body[1:]          # 丢掉 docstring，只看可执行代码
    code = ast.unparse(func)

    assert "_compact(check" not in code, "又在拿渲染后的展示字符串做比对"
    assert "_financial_comparable" in code, "必须复用已有的归一比较器，不要再写第二份"


def test_the_guard_would_catch_a_real_regression():
    """守卫必须真的会红（BC-61）——否则它只是一条永远通过的装饰。"""
    import ast

    src = ("def f(rows, check):\n"
           '    """说明。"""\n'
           "    return any(_compact(r['value']) != _compact(check['value']) for r in rows)\n")
    func = ast.parse(src).body[0]
    func.body = func.body[1:]
    assert "_compact(check" in ast.unparse(func), "判据抓不到真实回归，等于没有守卫"
