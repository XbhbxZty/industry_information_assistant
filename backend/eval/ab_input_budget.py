# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""BC-77 假设的单变量对照 —— 输入 24k 字 vs 47k 字，同一份材料

## 为什么整批观察答不了这个问题

阶段 2 的第二批 15 轮里，只有 case01 的语料超过 24000 字预算。
另外两个主体的语料 ~11000 字，**预算对它们根本不生效**——
两批送进模型的东西一模一样，它们对这个假设零信息量却照占分母。

真正有信息量的只有 case01 那 5 轮。5 轮全清在「失败率仍是 13%」
的假设下有 50% 的概率发生，**等于没测**。

## 这个脚本测的是什么

同一份 case01 语料，只改一个变量——总量预算：

| 臂 | 预算 | 相当于 |
|---|---|---|
| A | 24000 字 | BC-77 修复后的生产配置 |
| B | 不限（仅 40 条上界） | BC-77 修复前，实测约 47000 字 |

其余全部相同：同一批检索结果、同一个提示词、同一个输出上界、同一个模型。

## 交替发起，不是先跑完 A 再跑 B

供应商侧的行为随时间变化（负载、灰度、限流）。顺序跑两臂，
时间就成了与处理组完全共线的混杂变量——**测出来的差异分不清
是预算造成的还是那半小时造成的**。交替发起把时间摊平到两臂上。

## 样本量要在花钱之前算

脚本启动时先打印本次配置能分辨多大的差异。若 N 不足以分辨
「13% vs 0%」，它会直接说出来——**跑一个注定无结论的实验，
比不跑更糟，因为它会产出一个看起来像结论的数**。

用法：
    python eval/ab_input_budget.py --case case01 --n 20 --dry-run
    python eval/ab_input_budget.py --case case01 --n 20
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

#: B 臂的预算。取一个大到不会生效的数，让 40 条上界成为唯一约束——
#: 那正是 BC-77 修复前的状态。不写 47000 是因为**语料实际字数会变**，
#: 写死一个观测值会让 B 臂在语料变长时悄悄变成第三种配置。
UNBOUNDED_BUDGET = 10 ** 9

#: 待检验的效应量：第一批 15 轮实测 2 次抽取失败。
#: 写成常量而不是散在文字里——功效计算与判定表必须用同一个数。
BASELINE_EFFECT = 2 / 15

PROBES = ["主要客户 供应商 集中度", "产能 在建工程 项目进展",
          "行业地位 市场份额", "研发投入 专利", "经营情况 收入构成"]


def fisher_one_sided(a: int, b: int, c: int, d: int) -> float:
    """单侧 Fisher 精确检验。

    表格：
                失败   成功
        A 臂      a      b
        B 臂      c      d

    H1：B 臂（大输入）失败更多。返回 P(观测到当前或更极端的表格 | H0)。
    自己实现是为了不给评测脚本引入 scipy——**一个依赖换四行组合数不划算**。
    """
    row1, row2 = a + b, c + d
    col1 = a + c
    total = row1 + row2
    if total == 0:
        return 1.0

    def prob(x: int) -> float:
        return (math.comb(row1, x) * math.comb(row2, col1 - x)
                / math.comb(total, col1))

    lo = max(0, col1 - row2)
    # 「更极端」= A 臂失败数更少（等价于 B 臂失败数更多）
    return sum(prob(x) for x in range(lo, a + 1))


def _binom_tail(n: int, k: int, p: float) -> float:
    """P(X ≥ k)，X ~ Binom(n, p)。"""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(k, n + 1))


def _threshold(n: int) -> int | None:
    """A 臂 0 失败时，B 臂至少失败几次才够 p<0.05。"""
    return next((k for k in range(n + 1)
                 if fisher_one_sided(0, n, k, n - k) < 0.05), None)


def power(n: int, effect: float = BASELINE_EFFECT) -> float:
    """本次实验测出显著结果的**概率**。

    ⚠️ 这与「期望失败数下的 p 值」是两个数，且相差很大。
    N=40 时期望失败 5 次、p=0.027 看着很稳，但 B 臂真实率 13% 时
    实际失败数在 0~10 之间跳，**只有 61% 的概率落进显著区**。
    按 p 值决定跑不跑，会把 61% 的把握误当成 97%。
    """
    k = _threshold(n)
    return _binom_tail(n, k, effect) if k is not None else 0.0


def min_detectable(n: int) -> List[str]:
    """在花钱之前打印本次配置的功效，并给出够用的 N。"""
    out = ["## 本次配置的功效（先看这个再决定跑不跑）", "",
           f"待检验效应：A 臂真实失败率 0%，B 臂 {BASELINE_EFFECT:.0%}"
           f"（第一批实测值）。", ""]
    k = _threshold(n)
    if k is None:
        out += [f"⛔ **每臂 N={n} 时，B 臂即使全败也到不了 p<0.05。"
                f"这个实验不该跑。**", ""]
        return out

    pw = power(n)
    out += [f"- 每臂 N = **{n}**（共 {2 * n} 次调用）",
            f"- 需要 B 臂失败 **≥{k}** 次才够 p<0.05",
            f"- 本次功效：**{pw:.0%}**"
            f"（= 假设成立时，测出显著结果的概率）", ""]

    out += ["| 每臂 N | 需失败≥ | 功效 | 总调用 |", "|---:|---:|---:|---:|"]
    for cand in (20, 30, 40, 50, 60, 80):
        ck = _threshold(cand)
        mark = " ←本次" if cand == n else ""
        out.append(f"| {cand} | {ck} | {power(cand):.0%} | {2 * cand} |{mark}")
    out.append("")

    if pw < 0.8:
        need = next((c for c in range(n, 201) if power(c) >= 0.8), None)
        out += [
            f"- ⚠️ **功效 {pw:.0%} < 80%。** 这次实验有 {1 - pw:.0%} 的概率"
            f"在假设成立的情况下也测不出显著——**而「不显著」会被读成"
            f"「两者没差别」**，那是一个错的结论。",
            f"- 要到 80% 功效需要每臂 **{need}** 次（共 {2 * need} 次调用）。",
            "",
        ]
    else:
        out += [f"- 功效 {pw:.0%} ≥ 80%，配置够用。", ""]
    return out


async def _retrieve(scope) -> List[Dict[str, Any]]:
    """只检索一次，两臂共用同一批结果。

    分别检索会让两臂的材料不同，**那样测的就不是预算了**。
    """
    from service.deep_research_v2.agents.scout import DeepScout

    scout = DeepScout(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        search_api_key="")
    results: List[Dict[str, Any]] = []
    for query in PROBES:
        outcome = await scout._execute_local_search(query, kb_scope=scope)
        results.append(outcome.results)
    return results


def _build_arm(batches, budget: int) -> Tuple[List[Dict[str, Any]], int]:
    """用**生产的留存函数**建语料，只把预算换掉。

    另写一套留存逻辑就会在某个维度上与生产分叉，测出来的是一个
    不存在的系统（本 session 已撞三次）。所以这里 monkeypatch 常量，
    而不是复制那三十行。
    """
    from service.deep_research_v2.agents import scout as scout_mod

    original = scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET
    scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET = budget
    try:
        s = DeepScoutShim()
        state: Dict[str, Any] = {"due_diligence_mode": True, "raw_sources": []}
        for i, results in enumerate(batches):
            s.retain(state, results, f"probe{i}")
        sources = state["raw_sources"]
    finally:
        scout_mod.INVESTIGATION_INPUT_CHAR_BUDGET = original
    return sources, sum(len(x.get("summary") or "") for x in sources)


class DeepScoutShim:
    """只借 `_retain_corpus_for_investigation` 这一个未绑定方法。

    构造一个真的 DeepScout 需要 API key 与网络配置；而留存函数
    不碰 self 上的任何东西。用 shim 是为了**让两臂共用生产代码**，
    不是为了绕开它。
    """

    def retain(self, state, results, section_id):
        from service.deep_research_v2.agents.scout import DeepScout
        DeepScout._retain_corpus_for_investigation(self, state, results, section_id)


async def _one_call(wizard, prompt) -> Tuple[str, Dict[str, Any]]:
    from service.deep_research_v2.agents.wizard import CodeWizard
    return await wizard.call_llm(
        system_prompt=("你是尽职调查分析师的助手，只负责从给定材料中"
                       "摘录可溯源的事实。你的产出不进入授信决策，"
                       "不得给出风险判断或结论性评价。"),
        user_prompt=prompt, json_mode=True, temperature=0.2,
        max_tokens=CodeWizard.INVESTIGATION_MAX_TOKENS,
        timeout=CodeWizard.INVESTIGATION_TIMEOUT, return_meta=True)


async def run(case: str, n: int, dry: bool) -> int:
    from run_stage2_observation import SUBJECTS, _resolve
    from service import investigation_layer as inv
    from service.deep_research_v2.agents.wizard import CodeWizard
    from service.deep_research_v2.agents.scout import (
        INVESTIGATION_INPUT_CHAR_BUDGET)

    print("\n".join(min_detectable(n)))
    if dry:
        print("（--dry-run：只算分辨能力，不发起任何调用）")
        return 0

    entry = SUBJECTS[case]
    params = _resolve(entry)
    batches = await _retrieve(params["scope"])

    arms: Dict[str, Tuple[List[Dict[str, Any]], int]] = {
        "A_24k": _build_arm(batches, INVESTIGATION_INPUT_CHAR_BUDGET),
        "B_full": _build_arm(batches, UNBOUNDED_BUDGET),
    }
    for name, (srcs, chars) in arms.items():
        print(f"{name}：{len(srcs)} 条 / {chars} 字")

    # 两臂字数必须真的不同，否则这个实验测的是空气。
    if arms["A_24k"][1] >= arms["B_full"][1] * 0.95:
        print("⛔ 两臂输入字数几乎相同——**预算没有生效**，本次不产出任何结论。")
        print("   多半是这个主体的语料本来就没到预算。换一个语料更大的主体。")
        return 2

    prompts = {name: inv.EXPLORATORY_PROMPT.format(
        subject=params["subject_name"], as_of=params["as_of"],
        sources=inv.format_sources_for_prompt(srcs),
        max_findings=inv.MAX_FINDINGS) for name, (srcs, _) in arms.items()}

    wizard = CodeWizard(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"))

    fails: Counter = Counter()
    reasons: Counter = Counter()
    print()
    for i in range(1, n + 1):
        # ⚠️ 交替发起。顺序跑完一臂再跑另一臂，时间与处理组共线。
        for name in ("A_24k", "B_full"):
            try:
                content, meta = await _one_call(wizard, prompts[name])
            except Exception as exc:                       # noqa: BLE001
                fails[name] += 1
                reasons[(name, f"异常:{type(exc).__name__}")] += 1
                print(f"  #{i} {name:7s} 调用异常｜{type(exc).__name__}: "
                      f"{str(exc)[:90]}")
                continue
            raw = str(content or "").strip()
            ok = bool(wizard.parse_json_response(content))
            finish = str((meta or {}).get("finish_reason") or "未提供")
            tok = (meta or {}).get("completion_tokens")
            if not ok:
                fails[name] += 1
            reasons[(name, f"{'ok' if ok else '解析失败'}/{finish}")] += 1
            print(f"  #{i} {name:7s} {'成功' if ok else '失败'}"
                  f"｜finish={finish}｜{len(raw)} 字｜tok={tok}"
                  f"｜括号{'不平' if raw.count('{') > raw.count('}') else '配平'}")

    a, c = fails["A_24k"], fails["B_full"]
    p = fisher_one_sided(a, n - a, c, n - c)
    print(f"\nA_24k 失败 {a}/{n}（{a / n:.0%}）｜B_full 失败 {c}/{n}（{c / n:.0%}）")
    print(f"单侧 Fisher p = {p:.4f}")
    if p < 0.05:
        print("→ **大输入确实更容易失败**，BC-77 的假设得到支持。")
    else:
        print("→ **不显著。** 注意这不等于「两臂一样」——"
              "见开头的分辨能力表：本次 N 下多大的差异才测得出来。")
    print()
    for (name, label), count in sorted(reasons.items()):
        print(f"  {name:7s} {label:24s} × {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="case01")
    parser.add_argument("--n", type=int, default=20, help="每臂调用次数")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印分辨能力表，不发起调用")
    args = parser.parse_args()
    return asyncio.run(run(args.case, args.n, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
