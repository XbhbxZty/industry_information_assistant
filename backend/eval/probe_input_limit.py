# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""实测调查层所用模型的**输入上限**，而不是从一条报错里猜

## 为什么要单独测

`INVESTIGATION_INPUT_CHAR_BUDGET = 24000` 是我按一条报错定的：

    InternalError.Algo.InvalidParameter:
    Range of input length should be [1, 30720]

但那条报错只证明「47054 字会失败、23335 字会成功」，**它没有说
30720 的单位是字还是 token，也没有说这个数对哪个模型成立**。
我拿它当硬上限，然后据此把预算定在 24000，于是八章清单只有一章
能进模型（BC-78）——**一个没验证过的常量，决定了整个 B 层的覆盖面**。

这正是 BC-76 那条纪律：一条归因要么有判据，要么明说不知道。
我当时没测，现在补。

## 怎么测

用无意义但合法的中文填充，`max_tokens=16`，只问模型回一个 OK。
逐级加大输入，记录第一次失败的位置，再在成功/失败之间二分。

输出侧刻意压到最小——**这里测的是输入，不该让输出的花费混进来**。

用法：
    python eval/probe_input_limit.py                 # 默认梯度
    python eval/probe_input_limit.py --model qwen-plus
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

#: 一段真实感的中文，用来填充。不用「啊啊啊」是因为**重复字符的分词
#: 结果与真实文本差异很大**，测出来的边界会不适用于真语料。
FILLER = (
    "公司主要从事动力电池系统及储能系统的研发、生产与销售，产品覆盖乘用车、"
    "商用车与工商业储能等多个应用场景。报告期内，公司持续加大研发投入，"
    "在材料体系、结构设计与制造工艺等方向取得阶段性进展。同时，公司与多家"
    "整车厂商建立了长期合作关系，并在海外市场设立生产基地以支撑本地化交付。"
)

DEFAULT_STEPS = [20000, 24000, 28000, 30000, 32000, 40000, 60000, 100000, 200000]


def make_text(n: int) -> str:
    return (FILLER * (n // len(FILLER) + 1))[:n]


async def try_size(model: str, n: int) -> Tuple[bool, str]:
    from service.deep_research_v2.agents.wizard import CodeWizard

    wizard = CodeWizard(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    if model:
        wizard.model = model
    try:
        content, meta = await wizard.call_llm(
            system_prompt="只回复两个字符：OK",
            user_prompt=make_text(n) + "\n\n以上是背景材料。只回复 OK。",
            # ⚠️ `call_llm` 的 json_mode 默认是 True，而 DashScope 在 json
            #    模式下要求提示词里出现「json」字样，否则直接 400——
            #    那个 400 与「输入超限」的 400 是同一个错误码、同一个
            #    异常类型，**外观完全相同**（BC-51 的老形态）。
            #    测长度不需要 json 模式，显式关掉。
            json_mode=False,
            # 输出压到最小：这里测输入，不让输出花费混进来。
            max_tokens=16, temperature=0.0, timeout=180.0, return_meta=True)
        finish = str((meta or {}).get("finish_reason") or "?")
        return True, f"成功｜finish={finish}｜回复={str(content or '')[:20]!r}"
    except Exception as exc:                                # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


async def run(model: str, steps: List[int], bisect: bool) -> int:
    used = getattr(__import__("config.llm_config", fromlist=["x"]),
                   "get_agent_model")("wizard") if not model else model
    print(f"模型：{used}")
    print(f"端点：{os.getenv('LLM_BASE_URL') or os.getenv('DASHSCOPE_BASE_URL')}")
    print()

    last_ok: Optional[int] = None
    first_bad: Optional[int] = None
    for n in steps:
        ok, msg = await try_size(model, n)
        print(f"  {n:>7,} 字  {'✅' if ok else '❌'}  {msg}")
        if ok:
            last_ok = n
        else:
            first_bad = n
            break

    print()
    if first_bad is None:
        print(f"梯度内全部通过，最大试到 {last_ok:,} 字——**上限比试探范围还高**。")
        return 0
    if last_ok is None:
        print(f"最小的 {first_bad:,} 字就失败了，先查是不是别的问题。")
        return 1

    print(f"边界落在 {last_ok:,} ~ {first_bad:,} 字之间。")
    if not bisect:
        print("加 --bisect 可二分收敛。")
        return 0

    lo, hi = last_ok, first_bad
    while hi - lo > 1000:
        mid = (lo + hi) // 2
        ok, msg = await try_size(model, mid)
        print(f"  {mid:>7,} 字  {'✅' if ok else '❌'}  {msg}")
        lo, hi = (mid, hi) if ok else (lo, mid)
    print()
    print(f"实测上限：**{lo:,} ~ {hi:,} 字**之间。")
    print(f"当前 INVESTIGATION_INPUT_CHAR_BUDGET = 24000，"
          f"余量 {lo - 24000:+,} 字。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="", help="覆盖模型；缺省用 wizard 的配置")
    parser.add_argument("--bisect", action="store_true", help="在边界区间内二分")
    parser.add_argument("--steps", type=int, nargs="*", default=DEFAULT_STEPS)
    args = parser.parse_args()
    return asyncio.run(run(args.model, args.steps, args.bisect))


if __name__ == "__main__":
    raise SystemExit(main())
