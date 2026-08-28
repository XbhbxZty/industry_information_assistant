# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""挑战为什么这么少 —— 是没东西可判，还是判据压根够不着

## 要回答的问题

阶段 2 两批共 30 轮，跨层挑战只有 7 条，而 `mitigating_suppressed`
有 237 条。条数扫描把材料加到 3 倍，挑战仍然是 0。

**「没东西可判」与「判据够不着」的外观完全相同**——都是挑战数 0。
但它们指向相反的下一步：前者说明 B 层的价值不在产生挑战，
阶段 4 该缩小甚至不做；后者说明阶段 4 会在一个不会响的探测器上
定阈值，**跑完得到一组看起来很安全的参数，而它安全只是因为它什么都不做**。

这是 BC-73 那条纪律的原样复现：任何「全通过」的统计都要先问第二种可能。

## 怎么分辨

三条规则各自需要什么前提，就查那个前提在数据里满足了几次：

| 规则 | 前提 | 前提不满足时 |
|---|---|---|
| rule1 与已核实字段矛盾 | 发现要**绑上 field_id**，且该字段已核实 | 结构性地永不触发 |
| rule2 高风险落在未覆盖维度 | 维度未被清单覆盖 + aggravating + high | 同上 |
| rule3 mitigating 不得单独成立 | ——（这是抑制规则，不产生挑战） | — |

所以要数的是**前提的满足率**，不是结论的发生率。
结论为 0 可以有很多原因，前提为 0 只有一个原因。

用法：
    python eval/diagnose_challenge_drought.py --runs eval/runs/stage2 eval/runs/stage2_b2
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


def collect(roots: Iterable[Path]) -> List[Dict[str, Any]]:
    rows = []
    for root in roots:
        for path in sorted(root.glob("*/result.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            event = data.get("final_event") or {}
            box = event.get("investigation") or {}
            rows.append({
                "run": path.parent.name,
                "batch": root.name,
                "subject": data.get("subject_key") or "?",
                "findings": box.get("findings") or [],
                "verdict": event.get("cross_layer_verdict") or {},
                "checks": event.get("field_checks") or [],
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    rows = [r for r in collect(args.runs) if r["findings"] or r["verdict"]]
    if not rows:
        print("没有可分析的轮次")
        return 1

    out: List[str] = ["# 跨层挑战稀少的成因分析", ""]
    out += [f"样本：{len(rows)} 轮，"
            f"主体 {sorted({r['subject'] for r in rows})}", ""]

    # ---------------------------------------------------------- 一、前提满足率
    all_findings = [f for r in rows for f in r["findings"]]
    bound = [f for f in all_findings if f.get("field_id")]
    out += ["## 一、规则一的前提：发现有没有绑上清单字段", ""]
    out += [f"- 发现总数：**{len(all_findings)}**",
            f"- 绑上 `field_id` 的：**{len(bound)}**"
            f"（{len(bound) / max(1, len(all_findings)):.0%}）",
            f"- `field_id` 为空的：**{len(all_findings) - len(bound)}**"
            f"——**这些发现无论内容如何，规则一都不可能触发**", ""]

    # 绑上的字段，其 A 层核查状态是什么
    status_by_field: Dict[str, str] = {}
    for r in rows:
        for c in r["checks"]:
            fid = c.get("field_id") or c.get("field")
            if fid:
                status_by_field.setdefault(str(fid), str(c.get("status") or "?"))
    st = Counter(status_by_field.get(str(f.get("field_id")), "字段不在清单里")
                 for f in bound)
    if st:
        out += ["绑上的那些发现，对应字段在 A 层的核查状态：", "",
                "| 状态 | 发现数 |", "|---|---:|"]
        out += [f"| `{k}` | {v} |" for k, v in st.most_common()]
        out += ["", "> 规则一要求该字段**已核实**。状态不是 verified 的，"
                    "规则一同样不会触发——它会落进 `contradicts_unverified_field`。", ""]

    # ---------------------------------------------------------- 二、判定结果分布
    agg: Counter = Counter()
    for r in rows:
        agg.update({k: v for k, v in (r["verdict"].get("counts") or {}).items()
                    if isinstance(v, int)})
    out += ["## 二、判定结果的实际去向", "", "| 去向 | 次数 | 占发现数 |",
            "|---|---:|---:|"]
    total_f = agg.get("findings", len(all_findings)) or 1
    for key, n in agg.most_common():
        if key == "findings":
            continue
        out.append(f"| `{key}` | {n} | {n / total_f:.0%} |")
    out.append("")

    # ---------------------------------------------------------- 三、被抑制的是什么
    suppressed = []
    undecid = []
    for r in rows:
        for note in r["verdict"].get("notes") or []:
            tag = str(note.get("note") or "")
            if "mitigating" in tag:
                suppressed.append(note)
            elif "undecidable" in tag:
                undecid.append(note)

    # ---------------------------------------------------------- 二·五、规则二的前提
    agg_f = [f for f in all_findings if f.get("direction") == "aggravating"]
    high_f = [f for f in agg_f if f.get("materiality") == "high"]
    conf_f = [f for f in high_f if f.get("subject_confirmed")]
    cov_sets = [tuple(sorted(r["verdict"].get("covered_dimensions") or []))
                for r in rows]
    all_dims = Counter(str(f.get("dimension")) for f in all_findings)

    out += ["## 二·五、规则二的前提逐层筛", "",
            "| 条件 | 剩余发现 | 占比 |", "|---|---:|---:|",
            f"| aggravating | {len(agg_f)} | {len(agg_f) / total_f:.0%} |",
            f"| + materiality=high | {len(high_f)} | {len(high_f) / total_f:.0%} |",
            f"| + subject_confirmed | {len(conf_f)} | {len(conf_f) / total_f:.0%} |",
            "", "第四个条件是**维度未被清单覆盖**。实测各轮的 "
            "`covered_dimensions`：", ""]
    for cov, n in Counter(cov_sets).most_common():
        out.append(f"- {n} 轮：`{list(cov)}`")
    out += ["", f"而 B 层发现用到的维度：`{dict(all_dims)}`", "",
            "> **两个词表是同一套。** B 层只能用清单自己的类目给发现打标签，"
            "而规则二要求「落在清单没覆盖的维度」——"
            "**这个前提结构性地几乎不可能满足**。"
            "剩下能触发的只有个别轮次里恰好未覆盖的 `relation` / `opinion`，"
            "这与规则二实际只触发 3 次吻合。", ""]

    out += ["## 三、被抑制的 mitigating 发现长什么样", ""]
    if suppressed:
        dims = Counter(str(n.get("dimension")) for n in suppressed)
        mats = Counter(str(n.get("materiality")) for n in suppressed)
        out += [f"- 共 **{len(suppressed)}** 条",
                f"- 维度分布：{dict(dims)}",
                f"- 重要性分布：{dict(mats)}", ""]
        high = [n for n in suppressed if str(n.get("materiality")) == "high"]
        out += [f"- 其中 `materiality=high` 的：**{len(high)}** 条"
                f"{'——这些是最值得复核规则三是否过宽的' if high else '（没有高重要性的被抑制）'}",
                ""]
        for n in high[:5]:
            out.append(f"  - [{n.get('dimension')}] {str(n.get('claim'))[:90]}")
        out.append("")
    else:
        n_sup = agg.get("mitigating_suppressed", 0)
        out += [
            f"⚠️ **`counts` 说被抑制了 {n_sup} 条，但一条都没有留痕。**", "",
            "`cross_layer_verdict` 在规则三那一支是：", "",
            "```python",
            'if judgment["direction"] == "mitigating":',
            '    counts["mitigating_suppressed"] += 1',
            "    continue                     # ← 不写 note",
            "```", "",
            "所以**抑制了什么根本没存下来**，只存了抑制了多少。",
            "「没有记录」不等于「没有发生」——把仪表的缺口读成数据的结论，",
            "正是 BC-75「丢弃必须可见」那条纪律要防的事。", "",
            f"**结果：规则三是否过宽，用现有数据无法判断。** "
            f"要回答它，得先让那 {n_sup} 条留下痕迹。", ""]

    out += ["## 四、undecidable 的都是什么", ""]
    if undecid:
        out += [f"- 共 **{len(undecid)}** 条", ""]
        for n in undecid[:6]:
            out.append(f"- [{n.get('dimension')}／{n.get('direction')}] "
                       f"field_id={n.get('field_id')}｜"
                       f"{str(n.get('claim'))[:80]}")
        out.append("")
    else:
        out += ["无。", ""]

    # ---------------------------------------------------------- 结论口径
    unbound_rate = (len(all_findings) - len(bound)) / max(1, len(all_findings))
    out += ["## 判定", ""]
    if unbound_rate > 0.5:
        out += [f"- **{unbound_rate:.0%} 的发现没有绑上任何清单字段。**",
                "  规则一对它们**结构性地不可能触发**——不是"
                "「查了没矛盾」，是「压根没进入判定」。",
                "  阶段 4 若在此基础上定阈值，定的是一个大半输入都够不着的探测器。",
                ""]
    else:
        out += [f"- 绑定率 {1 - unbound_rate:.0%}，规则一的前提基本满足。"
                "挑战少不是绑定问题。", ""]
    if agg.get("mitigating_suppressed", 0) > 5 * max(1, agg.get("challenges", 0)):
        out += [f"- 抑制数（{agg.get('mitigating_suppressed')}）远高于挑战数"
                f"（{agg.get('challenges', 0)}）。规则三是否过宽，"
                f"要看上面「高重要性被抑制」那一节——"
                f"**若那里为 0，说明规则三拦掉的确实都是低价值的**。", ""]

    text = "\n".join(out)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
