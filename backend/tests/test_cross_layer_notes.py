# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""被抑制的发现要留痕，而且留痕的截断不得改变计数

## 这一轮在钉什么

阶段 2 两批 30 轮实测：`mitigating_suppressed` **237 条**，
跨层挑战 **7 条**——34 : 1。而被抑制的**内容**一条都没存下来：

    if judgment["direction"] == "mitigating":
        counts["mitigating_suppressed"] += 1
        continue                      # ← 不写 note

于是「规则三是不是拦掉了真信号」这个问题，用现有数据根本无法回答。
静默丢弃与「本来就没有」在外部完全同形——BC-75 的同一形态，
只不过这次丢的是判定结论而不是语料。

运行：cd backend && python -m pytest tests/test_cross_layer_notes.py -q
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from service.cross_layer_verdict import (  # noqa: E402
    MAX_NOTES, NOTE_MITIGATING_SUPPRESSED, judge_findings,
)


def _finding(claim: str, direction: str = "mitigating",
             dimension: str = "operation", materiality: str = "medium",
             **extra: Any) -> Dict[str, Any]:
    base = {"claim": claim, "direction": direction, "dimension": dimension,
            "materiality": materiality, "subject_confirmed": True,
            "source": {"title": "材料.txt", "url": "local://x"}}
    base.update(extra)
    return base


def _checks() -> List[Dict[str, Any]]:
    return [{"field_id": "revenue", "field_name": "营业收入",
             "category": "financial", "status": "verified",
             "value": "50120 万元"}]


# ------------------------------------------------- 一、被抑制的必须留痕


def test_被抑制的发现要留下痕迹():
    verdict = judge_findings([_finding("公司连续三年盈利")], _checks())
    assert verdict["counts"]["mitigating_suppressed"] == 1
    notes = verdict["notes"]
    assert notes, "抑制了却没有任何留痕——与「本来就没有」外观相同（BC-75）"
    assert notes[0]["note"] == NOTE_MITIGATING_SUPPRESSED
    assert "连续三年盈利" in notes[0]["claim"], "留痕要能认出是哪一条"


def test_留痕要带上判断依据而不只是标签():
    verdict = judge_findings(
        [_finding("客户集中度下降", dimension="relation")], _checks())
    note = verdict["notes"][0]
    for key in ("dimension", "direction", "materiality", "source", "why"):
        assert key in note, f"留痕缺少 {key}，复核时无从判断规则三是否过宽"
    assert note["direction"] == "mitigating"


def test_被抑制的不得混进挑战():
    """留痕不等于升级。规则三的语义没有变——只是变得可复核。"""
    verdict = judge_findings(
        [_finding("毛利率提升", materiality="high")], _checks())
    assert verdict["counts"]["challenges"] == 0
    assert not verdict["challenges"]
    assert verdict["triggers_human_review"] is False


def test_高重要性的利好同样被抑制且同样留痕():
    """规则三对 materiality 不设例外——但正因如此，更要留得下痕迹，
    否则「高重要性利好被拦掉」这件事永远不会被人看见。"""
    verdict = judge_findings(
        [_finding("获得国家级专精特新认定", materiality="high")], _checks())
    assert verdict["counts"]["mitigating_suppressed"] == 1
    high = [n for n in verdict["notes"]
            if n["note"] == NOTE_MITIGATING_SUPPRESSED
            and n["materiality"] == "high"]
    assert high, "高重要性的被抑制项必须可被检索出来"


# ------------------------------------------------- 二、截断不得改变计数


def test_notes_超上界要截断():
    findings = [_finding(f"利好第 {i} 条") for i in range(MAX_NOTES + 30)]
    verdict = judge_findings(findings, _checks())
    assert len(verdict["notes"]) == MAX_NOTES, "notes 无上界会让检查点线性膨胀"
    assert verdict["notes_truncated"] == 30, "截断了多少必须可见"


def test_截断不得改变计数():
    """**这条是本文件最重要的一条。**

    上界只该作用于留痕。若它同时改变 `counts`，那么一次为了控制
    检查点体积的截断，会悄悄改变统计结论——而统计结论正是
    阶段 4 要拿来做决策的东西。
    """
    n = MAX_NOTES + 30
    findings = [_finding(f"利好第 {i} 条") for i in range(n)]
    verdict = judge_findings(findings, _checks())
    assert verdict["counts"]["mitigating_suppressed"] == n, (
        f"计数被截断影响了：{verdict['counts']['mitigating_suppressed']} != {n}")
    assert verdict["counts"]["findings"] == n


def test_没有超界时不报截断():
    """恒定出现的字段不携带信息（BC-18 的形态）。"""
    verdict = judge_findings([_finding("利好一条")], _checks())
    assert verdict["notes_truncated"] == 0


# ------------------------------------------------- 三、夹具自检


def test_夹具确实会走到规则三():
    """先证明这组用例打的是规则三，而不是在别处就被拦下了。

    没有这条，上面每一条都可能因为发现在规则一就 continue 而空过。
    """
    verdict = judge_findings([_finding("利好一条")], _checks())
    assert verdict["counts"]["mitigating_suppressed"] == 1, (
        "夹具没走到规则三，上面的断言全部空过")
    assert verdict["counts"]["rule1_contradicts_verified_field"] == 0
