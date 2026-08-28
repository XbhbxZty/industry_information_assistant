# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""终局事件的计数字段必须能分辨是哪一层的

## 这一轮在钉什么

尽调一轮实测：

    日志       [CodeWizard] 调查层完成：{'charts_count': 4, ...}
    终局事件   charts_count: 0        ← 数的是 state["charts"]（A 层容器）
    实际       investigation.charts   4 张，全带来源

`charts_count` 数得**准确**（A 层容器在尽调模式下本来就空，B 层的图表
在 `investigation.charts`，物理隔离），但名字是通用的。读的人会理解成
「这轮产出了几张图」，拿到 0 就得出「图表没接上」——**这个误判真的发生过**。

与 BC-64 同形：*系统精确报告一组从未被尝试过的字段「覆盖率 0/14」，
数字正确，含义完全误导*。

运行：cd backend && python -m pytest tests/test_final_event_layer_labels.py -q
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from service.deep_research_v2.graph import build_complete_event  # noqa: E402


def _state(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "final_report": "报告正文", "quality_score": 8.0,
        "facts": [], "charts": [], "references": [], "iteration": 1,
        "completeness": {}, "field_checks": [], "risk_assessment": {},
        "evidence_store": {}, "errors": [], "as_of": "2026-08-20",
        "search_failures": [], "section_failures": [],
        "investigation": {"enabled": True, "charts": [], "findings": [],
                          "failures": [], "graph": {}},
    }
    base.update(over)
    return base


def _event(state):
    return build_complete_event(state, state.get("references", []))


# ------------------------------------------------- 核心


def test_尽调模式下两层的图表数分开给():
    """A 层容器空、B 层有 4 张——两个数必须都能读到。"""
    state = _state(charts=[], investigation={
        "enabled": True, "charts": [{"title": f"图{i}"} for i in range(4)],
        "findings": [], "failures": [], "graph": {}})
    event = _event(state)
    assert event["charts_count"] == 0, "A 层容器本就该是空的"
    assert event["investigation_charts_count"] == 4, (
        "B 层出了 4 张图，终局事件却读不出来——"
        "读的人只会看到 charts_count: 0（BC-64 同形）")


def test_普通研究模式下A层的数照常给():
    """反面：不能为了修 B 层把 A 层的数弄丢。"""
    state = _state(charts=[{"title": "甲"}, {"title": "乙"}],
                   investigation={"enabled": False, "charts": [],
                                  "findings": [], "failures": [], "graph": {}})
    event = _event(state)
    assert event["charts_count"] == 2
    assert event["investigation_charts_count"] == 0


def test_没有调查层时不报错():
    state = _state()
    del state["investigation"]
    event = _event(state)
    assert event["investigation_charts_count"] == 0


def test_调查层为None时不报错():
    event = _event(_state(investigation=None))
    assert event["investigation_charts_count"] == 0


# ------------------------------------------------- 契约不得被破坏


def test_既有字段一个都不能少():
    """加字段是安全的，改名或删字段不是——前端与评测脚本都在读它们。"""
    event = _event(_state())
    for key in ("type", "final_report", "quality_score", "facts_count",
                "charts_count", "iterations", "references", "completeness",
                "field_checks", "risk_assessment", "evidence_store",
                "errors", "as_of", "search_failures", "section_failures"):
        assert key in event, f"既有契约字段 {key} 丢了"


def test_夹具确实覆盖了会误导的那个组合():
    """先证明这组用例打中了真实场景：A 层 0 张、B 层非 0。

    没有这条，上面的断言可能在一个两边都是 0 的夹具上空过。
    """
    state = _state(charts=[], investigation={
        "enabled": True, "charts": [{"title": "图"}],
        "findings": [], "failures": [], "graph": {}})
    event = _event(state)
    assert event["charts_count"] == 0 and event["investigation_charts_count"] > 0, (
        "夹具没复现出「A 层 0、B 层非 0」这个会误导人的组合")
