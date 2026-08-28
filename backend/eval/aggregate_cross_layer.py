# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""聚合多轮运行的跨层一致性判定 —— 阶段 2 的验收装置

## 这个脚本存在的理由

阶段 2 的验收是「连续运行的不一致记录**可导出**，且每条都能指认具体依据」。
判定本身已经写进终局事件，但**散在几十个 result.json 里的记录不叫可导出**——
要回答的是"不一致率有多高、都是什么类型、有多少是口径差异"，
那需要把它们放在一起数。

## 它刻意不做的事

**不下结论。** 计划 9.3 给了一张中期检视的决策表（≤3 类可提前停、
≥4 类跑满 30 轮、不一致率 >90% 且集中于一类则先改设计），
本脚本把数字和对应的建议一起打出来，**但决定由人做**。

理由是那张表里第三行——"停下来先改设计"——是一个架构判断，
不该由一个统计脚本代劳。

## 为什么把"口径差异"单独计

不一致率里混着两种东西：真分歧，和两层口径不同导致的假分歧。
第四节整节都在讲这个区别。`contradicts_unverified_field`
（调查层指向一个清单没查过的字段）就是最典型的假分歧——
它看起来像矛盾，其实是 B 层在填空缺。**把它算进不一致率，
阶段 4 的决策就会建立在一个虚高的数上。**

用法：
    python eval/aggregate_cross_layer.py                    # 扫默认目录
    python eval/aggregate_cross_layer.py --runs DIR [DIR…]  # 指定目录
    python eval/aggregate_cross_layer.py --out report.md    # 同时写文件
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

BACKEND = Path(__file__).resolve().parents[1]

#: 计划 9.3 的预设观察规模与中期检视点。
PRESET_ROUNDS = 30
MIDPOINT_ROUNDS = 10

DEFAULT_RUN_ROOTS = [
    BACKEND / "eval" / "runs" / "mock_full",
    BACKEND / "eval" / "runs" / "stage2",
]


def _iter_results(roots: Iterable[Path]) -> List[Dict[str, Any]]:
    """收集每一轮的判定记录。

    只认 `result.json` 里的终局事件——**跳过没有判定的轮次并单独报数**。
    悄悄跳过会让分母缩小，把不一致率算高；而"这一轮压根没产出判定"
    本身就是需要被看见的事（阶段 1 那次运行就是这样）。
    """
    rows: List[Dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/result.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                rows.append({"run": path.parent.name, "error": str(exc)[:120]})
                continue
            event = data.get("final_event") or {}
            rows.append({
                "run": path.parent.name,
                # 产物自带的代码版本。老批次没有这个字段——**不要回填**，
                # "未记录"本身就是关于那批数据的真实信息。
                "code_rev": str(data.get("code_rev") or "未记录"),
                "batch": str(data.get("batch") or path.parent.parent.name),
                "subject": (event.get("risk_assessment") or {}).get("subject")
                           or data.get("subject") or "",
                "level": (event.get("risk_assessment") or {}).get("level") or "",
                "verdict": event.get("cross_layer_verdict") or {},
                "findings_total": len((event.get("investigation") or {})
                                      .get("findings") or []),
            })
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把多轮记录压成阶段 4 要用的那几个数。"""
    judged = [r for r in rows if r.get("verdict", {}).get("counts")]
    missing = [r for r in rows if r not in judged]

    challenged = [r for r in judged if r["verdict"]["verdict"] == "challenged"]
    by_rule: Counter = Counter()
    by_dimension: Counter = Counter()
    caliber: Counter = Counter()
    samples: List[Dict[str, Any]] = []

    for row in judged:
        counts = row["verdict"]["counts"]
        for key in ("rule1_contradicts_verified_field",
                    "rule2_high_aggravating_in_uncovered_dimension"):
            if counts.get(key):
                by_rule[key] += counts[key]
        for key in ("contradicts_unverified_field", "contradicts_unknown_field",
                    "mitigating_suppressed"):
            if counts.get(key):
                caliber[key] += counts[key]
        for item in row["verdict"].get("challenges") or []:
            by_dimension[item.get("dimension") or "unknown"] += 1
            if len(samples) < 8:
                samples.append({"run": row["run"], **item})

    rounds = len(judged)
    provenance: Counter = Counter()
    for row in rows:
        provenance[(row.get("batch") or "?", row.get("code_rev") or "未记录")] += 1

    return {
        "rounds_judged": rounds,
        "provenance": sorted(
            ({"batch": b, "code_rev": rev, "runs": n}
             for (b, rev), n in provenance.items()),
            key=lambda d: (d["batch"], d["code_rev"])),
        "rounds_missing_verdict": [r["run"] for r in missing],
        "challenged_rounds": len(challenged),
        "challenge_rate": (len(challenged) / rounds) if rounds else None,
        "distinct_challenge_types": len(by_rule),
        "by_rule": dict(by_rule),
        "by_dimension": dict(by_dimension),
        "caliber_only": dict(caliber),
        "samples": samples,
    }


def midpoint_guidance(summary: Dict[str, Any]) -> List[str]:
    """按计划 9.3 的决策表给出**建议**，不做决定。"""
    rounds = summary["rounds_judged"]
    rate = summary["challenge_rate"]
    types = summary["distinct_challenge_types"]
    lines: List[str] = []

    if rounds < MIDPOINT_ROUNDS:
        lines.append(f"- 已判定 {rounds} 轮，未到中期检视点（{MIDPOINT_ROUNDS} 轮），继续观察")
        return lines

    if rate is not None and rate > 0.9 and types <= 1:
        lines.append(
            f"- ⚠️ 不一致率 {rate:.0%} 且集中于同一类型——**建议停下来先改设计**。"
            f"继续跑只是重复记录同一个问题（计划 9.3 第三行，"
            f"也是第四节预判最可能发生的一种）")
    elif types <= 3:
        lines.append(
            f"- 不一致类型 {types} 种（≤3）——若最近 3 轮无新类型，**可提前停**，直接进阶段 4")
    else:
        lines.append(f"- 不一致类型 {types} 种（≥4）——**建议跑满 {PRESET_ROUNDS} 轮**")

    if rounds >= PRESET_ROUNDS:
        lines.append(f"- 已跑满预设 {PRESET_ROUNDS} 轮；请同时复盘这个预设值本身是否定高了")
    return lines


def _provenance_lines(summary: Dict[str, Any]) -> List[str]:
    """来源区。**多版本混算时，警告必须在数字之前出现**——

    放在末尾等于没放：读的人先看到 33%，再看到"其中一批语料是残缺的"，
    那个 33% 已经进脑子了。
    """
    prov = summary.get("provenance") or []
    if not prov:
        return []
    revs = {p["code_rev"] for p in prov}
    lines: List[str] = []
    if len(revs) > 1:
        lines += [
            "> ⚠️ **本汇总混合了 %d 个代码版本的运行结果。**"
            "不同版本下模型看到的输入不同（例如 BC-75 修复前后，"
            "同一份材料留存 1 片 vs 7 片），"
            "**把它们平均起来得到的数不描述任何一个真实配置**。"
            "阶段 4 的校准输入应当只取单一版本。" % len(revs),
            "",
        ]
    lines += ["| 批次 | 代码版本 | 轮次 |", "|---|---|---:|"]
    lines += [f"| {p['batch']} | `{p['code_rev']}` | {p['runs']} |" for p in prov]
    lines.append("")
    return lines


def render(summary: Dict[str, Any]) -> str:
    rate = summary["challenge_rate"]
    lines = [
        "# 跨层一致性观察汇总（阶段 2）", "",
        "> 本汇总只呈现数字与对应建议，**结论由人做**——"
        "「停下来先改设计」是架构判断，不该由统计脚本代劳。", "",
        *_provenance_lines(summary),
        f"- 已判定轮次：**{summary['rounds_judged']}**"
        f"（预设 {PRESET_ROUNDS}，中期检视点 {MIDPOINT_ROUNDS}）",
        f"- 存在实质挑战的轮次：**{summary['challenged_rounds']}**"
        + (f"（{rate:.0%}）" if rate is not None else ""),
        f"- 不一致类型数：**{summary['distinct_challenge_types']}**",
    ]
    if summary["rounds_missing_verdict"]:
        lines.append(
            f"- ⚠️ {len(summary['rounds_missing_verdict'])} 轮没有判定记录，"
            f"**未计入分母**：{'、'.join(summary['rounds_missing_verdict'][:6])}")

    if summary["by_rule"]:
        lines += ["", "## 按规则", "", "| 规则 | 次数 |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(
            summary["by_rule"].items(), key=lambda kv: -kv[1])]

    if summary["by_dimension"]:
        lines += ["", "## 按维度", "", "| 维度 | 次数 |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(
            summary["by_dimension"].items(), key=lambda kv: -kv[1])]

    lines += ["", "## 不计入不一致的口径差异", "",
              "> 这几类看起来像矛盾，其实不是。把它们算进不一致率，"
              "阶段 4 的决策就会建立在一个虚高的数上。", ""]
    if summary["caliber_only"]:
        lines += ["| 类型 | 次数 |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(
            summary["caliber_only"].items(), key=lambda kv: -kv[1])]
    else:
        lines.append("（本批次没有出现）")

    if summary["samples"]:
        lines += ["", "## 样本（每条都能指认依据）", ""]
        for item in summary["samples"]:
            lines.append(f"- **[{item['run']}]** {item['claim'][:80]}")
            lines.append(f"　规则：{item['rule']}｜依据：{item['why'][:110]}")

    lines += ["", "## 中期检视建议（计划 9.3）", ""]
    lines += midpoint_guidance(summary)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="*", default=None,
                        help="运行目录；缺省扫 eval/runs 下的 mock_full 与 stage2")
    parser.add_argument("--out", type=Path, default=None, help="同时写入的 Markdown 文件")
    args = parser.parse_args()

    roots = args.runs if args.runs else DEFAULT_RUN_ROOTS
    rows = _iter_results(roots)
    if not rows:
        print(f"没有找到任何 result.json：{[str(r) for r in roots]}")
        return 1

    summary = summarize(rows)
    text = render(summary)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
