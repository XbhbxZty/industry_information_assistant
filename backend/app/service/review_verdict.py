# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
审核裁决规则（纯函数，无 LLM）—— BC-29 修复

## 问题

Critic 此前**自己报 verdict**：

```python
verdict = review_result.get("overall_assessment", {}).get("verdict", "needs_revision")
if verdict == "pass":
    state["phase"] = ResearchPhase.COMPLETED.value      # 报告直接出厂
```

而"quality_score >= 7 才能给 pass"这条约束**只写在提示词里，代码不校验**。
盲测实测到的形态（BC-29）：模型在 `issues` 里正确指出了危险外推，
却给了 `severity=minor` + `verdict=pass`，报告照常放行。

**它看见了，然后自己放过了自己。**

## 修法：把裁决权从模型收回代码

沿用项目一贯的分工——**LLM 负责把非结构化信息转成结构化字段，
规则负责判断**（与 `risk_scorecard.py` 同一原则）。
模型仍然产出 issues 列表，但"这份报告能不能过"由规则算。

### 为什么这不是第二个扫描器（BC-47）

扫描器是拿正则做**开集检测**——"中文里有多少种把未核实写成无记录的说法"
没有边界，词表永远补不完，三轮都在过拟合开发集。

本模块做的是对**已抽取的结构化数据**做**闭集规则**：输入是 issues 列表
（模型已经完成了自然语言→结构化的转换），输出是三选一的裁决。
没有文本匹配，没有词表，可穷举、可单测、零方差。

**两者唯一相似的只是"确定性"这三个字。**

## 一条关键取舍：尽调核心问题类型不接受 severity 降级

对 `unverified_as_fact` / `conflict_silently_resolved` /
`unsupported_risk_conclusion` 这三类，**只要模型说存在，无论它标什么严重度，
都不得自动通过**。

理由：整套反幻觉架构就是为了抓这三类。允许模型用 `minor` 决定它们是否阻断，
等于把最后的裁决权又交回给刚刚被证明会放水的那一方——BC-29 本体。

代价是更多报告被拦下转人工。但 v0.6 的复核卡点已经建好，
拦下的成本是一次人工确认；放过的成本是一笔无人复核的授信。
**失败方向不对称时，倒向保守的一侧。**
"""
from typing import Any, Dict, List, Optional

VERDICT_PASS = "pass"
VERDICT_NEEDS_REVISION = "needs_revision"
VERDICT_MAJOR_ISSUES = "major_issues"

# 由松到严。取"至少为 X"时按索引比较。
VERDICT_ORDER = [VERDICT_PASS, VERDICT_NEEDS_REVISION, VERDICT_MAJOR_ISSUES]

# 尽调场景的核心问题类型：整套反幻觉架构就是为了抓它们。
# **不接受 severity 降级**——模型说存在即阻断。
DD_BLOCKING_ISSUE_TYPES = frozenset({
    "unverified_as_fact",           # 把未核实字段当事实断言
    "conflict_silently_resolved",   # 多源冲突被单方面采信
    "unsupported_risk_conclusion",  # 风险结论无证据支撑
    "review_not_executed",          # 审核链路本身没跑（降级路径）
})

MIN_PASS_SCORE = 7.0


def _at_least(current: str, floor: str) -> str:
    ci = VERDICT_ORDER.index(current) if current in VERDICT_ORDER else 0
    fi = VERDICT_ORDER.index(floor)
    return VERDICT_ORDER[max(ci, fi)]


def derive_verdict(
    issues: Optional[List[Dict[str, Any]]],
    quality_score: Any,
    llm_verdict: Optional[str] = None,
) -> Dict[str, Any]:
    """
    按 issues 列表推导裁决，忽略模型自报的 verdict。

    规则按严格程度叠加，最严的胜出：

    1. 尽调核心类型问题（任何严重度）        → major_issues
    2. 任何 critical 问题                    → major_issues
    3. 任何 major 问题                       → needs_revision
    4. quality_score < 7                     → 至少 needs_revision
    5. 以上都不触发                          → pass

    Returns:
        除 `verdict` 外还带 `llm_verdict` 与 `verdict_reasons`。
        **模型的原始判断必须保留**：与人工复核改写等级同理（BC-22 / v0.6），
        谁做的判断、依据什么，事后必须能分辨。
    """
    issues = [i for i in (issues or []) if isinstance(i, dict)]
    reasons: List[str] = []
    verdict = VERDICT_PASS

    # 1) 尽调核心类型：不看 severity
    blocking = [i for i in issues
                if i.get("issue_type") in DD_BLOCKING_ISSUE_TYPES]
    if blocking:
        verdict = _at_least(verdict, VERDICT_MAJOR_ISSUES)
        for i in blocking:
            reasons.append(
                f"{i.get('issue_type')}（模型标记为 {i.get('severity', '未标注')}）："
                f"该类问题不接受严重度降级，一律阻断自动通过"
            )

    # 2) 任何 critical
    crit = [i for i in issues if i.get("severity") == "critical"
            and i.get("issue_type") not in DD_BLOCKING_ISSUE_TYPES]
    if crit:
        verdict = _at_least(verdict, VERDICT_MAJOR_ISSUES)
        reasons.append(f"存在 {len(crit)} 条 critical 问题")

    # 3) 任何 major
    major = [i for i in issues if i.get("severity") == "major"
             and i.get("issue_type") not in DD_BLOCKING_ISSUE_TYPES]
    if major:
        verdict = _at_least(verdict, VERDICT_NEEDS_REVISION)
        reasons.append(f"存在 {len(major)} 条 major 问题")

    # 4) 分数门槛。此前只写在提示词里，代码从不校验——BC-29 的直接成因之一
    try:
        score = float(quality_score)
    except (TypeError, ValueError):
        score = 0.0
    if score < MIN_PASS_SCORE:
        verdict = _at_least(verdict, VERDICT_NEEDS_REVISION)
        reasons.append(f"质量分 {score} 低于通过线 {MIN_PASS_SCORE}")

    if not reasons:
        reasons.append("未发现阻断级问题且质量分达标")

    return {
        "verdict": verdict,
        "verdict_source": "rule",
        # 模型自报的判断原样保留，便于事后分辨"规则算的"与"模型说的"
        "llm_verdict": llm_verdict,
        "llm_verdict_overridden": bool(llm_verdict and llm_verdict != verdict),
        "verdict_reasons": reasons,
    }


def is_blocking(verdict: str) -> bool:
    """该裁决是否应阻止报告进入完成态。"""
    return verdict != VERDICT_PASS


def unresolved_blocking_issues(issues: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    仍然阻断的尽调核心问题。

    供编排层在**达到最大迭代次数**时使用：迭代用尽不等于问题解决了，
    此时强制完成必须同时强制人工复核，不能静默出厂。
    """
    return [i for i in (issues or []) if isinstance(i, dict)
            and i.get("issue_type") in DD_BLOCKING_ISSUE_TYPES]
