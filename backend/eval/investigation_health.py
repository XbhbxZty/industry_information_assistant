# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""调查层装置健康度 —— 抽取是否成功、语料留存多少、发现分布

## 与 `aggregate_cross_layer.py` 的分工

那个脚本回答**「两层一致不一致」**，是阶段 2 的观察目标。
本脚本回答**「装置本身工作没工作」**，是那个观察的前提条件。

分成两个是因为它们的失效模式互不相同，而且**混在一起会互相掩盖**：
一轮抽取失败会产出 0 条发现，而 0 条发现在一致性统计里表现为
「完全一致」——BC-74 记的正是这件事。**装置坏了看起来像两层特别和谐。**

## 判定口径先写死，再看数据

第一批 15 轮里 2 轮抽取失败（13%）。BC-77 把送进模型的正文从
47054 字压到 24000 以内，假设是「输入压在 30720 上限导致供应商降级」。

要判定这个假设，必须**在看到第二批数据之前**把判据钉死，否则
看完再定阈值，是在用数据挑一个能支持既有结论的规则。

判据（H0：真实失败率仍为 13%）：

| 第二批观测 | H0 下的概率 | 判定 |
|---|---|---|
| 0 失败 / 15 轮 | 0.87¹⁵ = 12.4% | **不足以否定 H0**——但与「已修复」不矛盾 |
| 0 失败 / 15 轮 + 探针 12 次 | 0.87²⁷ = 2.4% | 可以说「大概率已修复」，前提是两条路径同源 |
| ≥2 失败 / 15 轮 | — | **BC-77 不是成因**，继续查 |
| 1 失败 / 15 轮 | 0.87¹⁵×… ≈ 40% 累计 | 无结论，需要更多轮次 |

⚠️ 探针与生产流水线**不必然同源**：探针是单次抽取，流水线还有并发、
上下文累积、多章节循环。把两者的次数直接相加是一个**假设**，
不是一个事实——所以两行分开报，合并那一行明确标注前提。

用法：
    python eval/investigation_health.py --runs eval/runs/stage2_b2
    python eval/investigation_health.py --runs eval/runs/stage2 eval/runs/stage2_b2
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

#: 第一批实测的抽取失败率，作为 H0。写成常量是为了让判据可被复核，
#: 而不是每次读的人自己心算一个印象。
BASELINE_FAILURE_RATE = 2 / 15


def _pow(p: float, n: int) -> float:
    return (1.0 - p) ** n


def collect(roots: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/result.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                rows.append({"run": path.parent.name, "batch": root.name,
                             "broken": str(exc)[:100]})
                continue
            event = data.get("final_event") or {}
            box = event.get("investigation") or {}
            drops = event.get("investigation_corpus_drops") or []
            failures = box.get("failures") or []
            # ⚠️ `failures` 是**统一留痕列表**，不是失败列表（BC-51）。
            #    `not_found`（查了没有）在里面占绝大多数，且是正常产出——
            #    「该字段未核实，不得绘图」正是系统该说的话。
            #    只有 `kind == "error"` 才是装置没跑成。
            errors = [f for f in failures if str(f.get("kind")) == "error"]
            rows.append({
                "run": path.parent.name,
                "batch": str(data.get("batch") or root.name),
                "code_rev": str(data.get("code_rev") or "未记录"),
                "subject_key": str(data.get("subject_key") or ""),
                "has_verdict": bool((event.get("cross_layer_verdict") or {})
                                    .get("counts")),
                "findings": len(box.get("findings") or []),
                "failures": failures,
                "errors": errors,
                "kinds": Counter(str(f.get("kind") or "?") for f in failures),
                "error_stages": [str(f.get("stage") or "?") for f in errors],
                # 语料读数取各章最大值：留存是累加的，最后一章看到的就是总量。
                "retained": max((int(d.get("retained") or 0) for d in drops),
                                default=0),
                "retained_chars": max((int(d.get("retained_chars") or 0)
                                       for d in drops), default=0),
                "dropped_over_budget": sum(int(d.get("dropped_over_budget") or 0)
                                           for d in drops),
                "dropped_duplicate": sum(int(d.get("dropped_duplicate") or 0)
                                         for d in drops),
            })
    return rows


def render(rows: List[Dict[str, Any]]) -> str:
    good = [r for r in rows if not r.get("broken")]
    out: List[str] = ["# 调查层装置健康度", ""]
    if not good:
        return "\n".join(out + ["没有可读的 result.json。"])

    by_batch: Dict[str, List[Dict[str, Any]]] = {}
    for row in good:
        by_batch.setdefault(row["batch"], []).append(row)

    out += ["| 批次 | 代码版本 | 轮次 | 抽取失败轮 | 失败率 | 0 发现轮 | "
            "语料中位(片/字) | 超预算丢弃 |", "|---|---|---:|---:|---:|---:|---|---:|"]
    for batch, group in sorted(by_batch.items()):
        revs = sorted({r["code_rev"] for r in group})
        failed = [r for r in group if r["errors"]]
        zero = [r for r in group if r["findings"] == 0]
        retained = sorted(r["retained"] for r in group)
        chars = sorted(r["retained_chars"] for r in group)
        mid = len(group) // 2
        out.append(
            f"| {batch} | {'/'.join(f'`{v}`' for v in revs)} | {len(group)} | "
            f"{len(failed)} | {len(failed) / len(group):.0%} | {len(zero)} | "
            f"{retained[mid]}/{chars[mid]} | "
            f"{sum(r['dropped_over_budget'] for r in group)} |")
    out.append("")

    kinds: Counter = Counter()
    for row in good:
        kinds.update(row["kinds"])
    out += ["## 留痕分布（三类 kind 必须分开看）", "",
            "> `not_found` 是**正常产出**——「查了没有」；只有 `error` 是装置没跑成。"
            "合并成一个数，一切正常会被读成全线崩溃（BC-51）。", "",
            "| kind | 次数 |", "|---|---:|"]
    out += [f"| `{k}` | {n} |" for k, n in kinds.most_common()]
    out.append("")

    errs = [(r["run"], item) for r in good for item in r["errors"]]
    if errs:
        out += ["## error 明细（这才是那 13%）", ""]
        for run, item in errs:
            out.append(f"- **[{run}]** {item.get('stage')}｜{item.get('reason')}")
            detail = str(item.get("detail") or "")
            if detail:
                out.append(f"  　留痕：{detail[:420]}")
        out.append("")
    else:
        out += ["## error 明细（这才是那 13%）", "",
                "本次收集的轮次中**没有 `kind=error` 的留痕**。", ""]

    # 0 发现必须单独报：它与「抽取失败」不同，也与「材料里真没东西」不同，
    # 而三者的外观在一致性统计里完全相同（BC-74、BC-51）。
    zero_no_fail = [r for r in good if r["findings"] == 0 and not r["errors"]]
    if zero_no_fail:
        out += ["> ⚠️ 有 %d 轮产出 0 条发现但**没有失败留痕**。"
                "这既可能是材料里真的没有可摘录的事实，也可能是一条"
                "没有被留痕捕获的失败路径——**外观相同**（BC-51）。"
                "逐轮点开 events.jsonl 才能区分：%s"
                % (len(zero_no_fail), "、".join(r["run"] for r in zero_no_fail)),
                ""]

    out += _verdict_lines(by_batch)
    return "\n".join(out)


def _effective_sample_lines(group: List[Dict[str, Any]]) -> List[str]:
    """报出**这批里有几轮真的被 BC-77 改变了输入**。

    预算 24000 只对超过它的主体起作用。语料 11005 字的主体，
    两批送进模型的东西一模一样——它对这个假设**零信息量**，
    但它照样占着分母，把置信度撑得比实际高。
    """
    bit = [r for r in group if r["dropped_over_budget"] > 0]
    per_subject: Dict[str, List[int]] = {}
    for row in group:
        per_subject.setdefault(row["subject_key"] or "?", []).append(
            row["retained_chars"])

    out = ["**有效样本（预算是否真的生效）**", "",
           "| 主体 | 轮次 | 语料字数 | 预算是否生效 |", "|---|---:|---|---|"]
    for key, chars in sorted(per_subject.items()):
        lo, hi = min(chars), max(chars)
        span = f"{lo}" if lo == hi else f"{lo}–{hi}"
        hit = any(r["dropped_over_budget"] > 0
                  for r in group if r["subject_key"] == key)
        out.append(f"| {key} | {len(chars)} | {span} | "
                   f"{'**是**（两批输入不同）' if hit else '否（两批输入相同）'} |")
    out += ["",
            f"- 对 BC-77 假设**有信息量**的轮次：**{len(bit)} / {len(group)}**",
            ""]
    if not bit:
        out += [
            "- ⚠️ **没有任何一轮触发了总量预算。** 也就是说这批的输入与批次 1 "
            "在字数上没有实质差别——**它无法判定 BC-77 的假设**，"
            "无论失败率是多少。若批次 1 的失败也发生在这些主体上，"
            "那些失败从一开始就不是输入长度造成的。",
            "",
        ]
    return out


def _verdict_lines(by_batch: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """按**预先写死的**判据给结论，不看数据再定阈值。"""
    out = ["## 对 BC-77 假设的判定", "",
           f"H0：抽取失败率仍为第一批实测的 {BASELINE_FAILURE_RATE:.0%}。", ""]
    fresh = {b: g for b, g in by_batch.items()
             if any(r["code_rev"] != "未记录" for r in g)}
    if not fresh:
        out.append("本次收集里没有带 `code_rev` 的批次——**无法判定**。"
                   "只有 BC-77 修复之后跑的批次才对这个假设有信息量。")
        return out

    for batch, group in sorted(fresh.items()):
        n = len(group)
        k = len([r for r in group if r["errors"]])
        out.append(f"### {batch}：{k} 失败 / {n} 轮")
        out.append("")
        out += _effective_sample_lines(group)
        if k == 0:
            p = _pow(BASELINE_FAILURE_RATE, n)
            out.append(f"- H0 下出现 {n} 轮全清的概率：**{p:.1%}**"
                       f"（按全部轮次算；若只算预算生效的轮次，"
                       f"把握比这个数还低）")
            if p > 0.05:
                out += [
                    f"- {p:.1%} > 5%：**不足以否定 H0**。这批数据与「已修复」"
                    f"不矛盾，但同样与「运气好」不矛盾——两种解释外观相同。",
                    "- 若要合并探针的 12 次（共 %d 次）："
                    "H0 下概率降到 **%.1f%%**。"
                    "⚠️ 前提是探针与流水线同源；探针是单次抽取，"
                    "流水线还有并发、上下文累积与多章节循环，"
                    "**这个前提没有被验证过**。"
                    % (n + 12, _pow(BASELINE_FAILURE_RATE, n + 12) * 100),
                ]
            else:
                out.append(f"- {p:.1%} ≤ 5%：可以说 H0 被这批数据否定。")
        elif k >= 2:
            out += [f"- 失败率 {k / n:.0%}，与第一批同量级——"
                    f"**BC-77 不是那 13% 的成因**，继续查。",
                    "- 下一步看失败归因表：若归因仍是「响应不完整但另有成因」"
                    "（BC-76 的第三条），说明成因在输入长度之外。"]
        else:
            out.append(f"- 1 失败 / {n} 轮：**无结论**。"
                       f"这个观测在 H0 和「已修复」下都常见，需要更多轮次。")
        out.append("")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    rows = collect(args.runs)
    if not rows:
        print(f"没有找到 result.json：{[str(r) for r in args.runs]}")
        return 1
    text = render(rows)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
