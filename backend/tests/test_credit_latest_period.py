# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""额度测算必须取**期间最近**的一期，而不是列表最后一项

## 这一轮在钉什么

原实现：

    def _latest(company):
        fins = company.get("financials") or []
        return fins[-1] if fins else {}

函数叫 `_latest`，做的是「取最后一项」。两者相等的前提是
**档案按「最老在前」排列**——而这个约定没有写在函数文档里、
没有写在档案 schema 里、没有任何校验。

两个 mock 档案（2023/2024/2025）恰好是那个顺序，所以从未暴露。
2026-08-24 接进一份按财报惯例排「最新在前」的真实档案，实测：

    营收法   108,903.05 = 544,515.26 × 20%   ← 2022 年营收
    现金流法 288,000.00 =  96,000.00 × 3.0   ← 2022 年现金流

而最近期是 2024 年：营收 779,891 万、经营现金流 191,700 万。
**授信额度低估一半，而且完全静默**——64797–87667 万元
这个数字看不出任何异常。

运行：cd backend && python -m pytest tests/test_credit_latest_period.py -q
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from service.credit_advice import _latest, _period_year  # noqa: E402


def _fin(period: str, revenue: float) -> Dict[str, Any]:
    return {"period": period, "revenue": revenue, "net_profit": 1.0,
            "total_assets": 10.0, "total_liabilities": 5.0,
            "operating_cash_flow": 2.0, "unit": "万元"}


NEWEST_FIRST: List[Dict[str, Any]] = [
    _fin("2024年度", 779891.40), _fin("2023年度", 631196.38),
    _fin("2022年度", 544515.26),
]
OLDEST_FIRST: List[Dict[str, Any]] = list(reversed(NEWEST_FIRST))


# ------------------------------------------------- 核心：顺序不得影响结果


def test_最新在前时取到最近期():
    """**这条是本文件的直接回归。** 原实现在这里取到 2022 年。"""
    got = _latest({"financials": NEWEST_FIRST})
    assert got["period"] == "2024年度", (
        f"取到了 {got['period']}——按位置取而不是按期间取，"
        f"额度会用三年前的数字算（低估一半且静默）")
    assert got["revenue"] == 779891.40


def test_最老在前时同样取到最近期():
    """反面：不能为了修「最新在前」把原来能跑的顺序弄坏。

    两个既有 mock 档案就是这个顺序，它们此前是**碰巧**正确的。
    """
    got = _latest({"financials": OLDEST_FIRST})
    assert got["period"] == "2024年度"
    assert got["revenue"] == 779891.40


def test_乱序也取到最近期():
    """真正的判据是「与顺序无关」，不是「支持这两种顺序」。"""
    shuffled = [NEWEST_FIRST[1], NEWEST_FIRST[2], NEWEST_FIRST[0]]
    assert _latest({"financials": shuffled})["period"] == "2024年度"


def test_两种顺序结果必须逐位相同():
    """把「无关」写成断言：同一批数据，换个顺序结果不得有任何差别。"""
    assert _latest({"financials": NEWEST_FIRST}) == \
        _latest({"financials": OLDEST_FIRST})


# ------------------------------------------------- 夹具自检


def test_夹具能让按位置取的实现露馅():
    """先证明这批夹具有鉴别力：首尾两项确实不同。

    若夹具里三期营收相同，上面每条断言在错误实现下也会绿——
    本项目已两次被等长/同值夹具坑过（BC-77、BC-78）。
    """
    assert NEWEST_FIRST[0]["revenue"] != NEWEST_FIRST[-1]["revenue"], (
        "夹具首尾同值，按位置取与按期间取结果一样，测不出东西")
    assert NEWEST_FIRST[-1]["period"] == "2022年度", (
        "夹具末项不是最老的一期，复现不出成因")


# ------------------------------------------------- 边界


def test_空档案返回空字典():
    assert _latest({}) == {}
    assert _latest({"financials": []}) == {}


def test_期间解析不出年份时退回末项且不崩():
    """一期都解析不出年份是异常输入，退回原行为但必须出声（见实现里的 warning）。"""
    weird = [{"period": "上一会计期间", "revenue": 1.0},
             {"period": "本会计期间", "revenue": 2.0}]
    got = _latest({"financials": weird})
    assert got["revenue"] == 2.0


def test_年份解析():
    assert _period_year({"period": "2024年度"}) == 2024
    assert _period_year({"period": "2024年1-6月"}) == 2024
    assert _period_year({"period": "无期间"}) == -1
    assert _period_year({}) == -1


def test_部分期间无年份时仍取有年份里最近的():
    mixed = [{"period": "2023年度", "revenue": 5.0},
             {"period": "未标注", "revenue": 9.0},
             {"period": "2025年度", "revenue": 7.0}]
    assert _latest({"financials": mixed})["revenue"] == 7.0
