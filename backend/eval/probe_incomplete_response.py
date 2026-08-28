# Copyright © 2026 XbhbxZty
"""复现「响应不完整但远未达输出上界」，并实测 finish_reason

## 为什么需要它

阶段 2 观察 15 轮里有 2 轮（13%）响应不完整、解析失败、损失全部发现。
BC-76 修正了归因措辞，但**成因仍未查明**——因为 `call_llm` 当时把
`finish_reason` 丢掉了。仪表补上之后，这个脚本负责把读数取回来。

## 为什么是重复调用而不是再跑流水线

失败是间歇的（2/15）。跑一轮完整流水线要几分钟、十几次模型调用，
而这里只需要**同一份输入反复调同一个抽取**。隔离成单次调用，
同样的钱能多跑一个数量级的样本（BC-63「先补仪表，再动手」）。

用法：
    python eval/probe_incomplete_response.py --case case01 --tries 8
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")


async def _build_corpus_like_production(scope, queries):
    """按**生产路径**构建语料：分片去重 + 条数上界 + 总量预算。

    直接调 `_retain_corpus_for_investigation`，不另写一套——
    另写一套就会在某个维度上与生产分叉，而分叉之后测出来的
    是一个不存在的系统（本 session 已撞三次）。
    """
    import os as _os

    from service.deep_research_v2.agents.scout import DeepScout

    scout = DeepScout(
        llm_api_key=_os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=_os.getenv("LLM_BASE_URL",
                                "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        search_api_key="")
    state = {"due_diligence_mode": True, "raw_sources": []}
    for query in queries:
        outcome = await scout._execute_local_search(query, kb_scope=scope)
        scout._retain_corpus_for_investigation(state, outcome.results, "probe")
    return state["raw_sources"], state.get("investigation_corpus_drops") or []


async def _probe(case: str, tries: int, limit: int) -> int:
    from run_stage2_observation import SUBJECTS, _resolve
    from service import investigation_layer as inv
    from service.deep_research_v2.agents.wizard import CodeWizard

    entry = SUBJECTS[case]
    params = _resolve(entry)
    probes = ["主要客户 供应商 集中度", "产能 在建工程 项目进展",
              "行业地位 市场份额", "研发投入 专利", "经营情况 收入构成"]
    sources, drops = await _build_corpus_like_production(params["scope"], probes)
    if drops:
        print(f"语料丢弃：重复 {sum(d['dropped_duplicate'] for d in drops)}"
              f"／超条数 {sum(d['dropped_over_limit'] for d in drops)}"
              f"／超总量 {sum(d.get('dropped_over_budget', 0) for d in drops)}")
    total_chars = sum(len(s["summary"]) for s in sources)
    print(f"主体：{params['subject_name']}｜来源 {len(sources)} 条｜正文 {total_chars} 字")
    print(f"输出预算 {CodeWizard.INVESTIGATION_MAX_TOKENS} token")

    # ⚠️ 输入无效时必须**失败**，不能产出一个看起来正常的零。
    #
    # 第一次跑这个探针就撞上了：Milvus 停了、检索 0 条，模型收到空材料，
    # 8 次全部秒回空数组，脚本报"失败 0/8"。那不是"未复现"，
    # 是根本没跑要测的那件事——而输出看起来完全正常。
    #
    # 消融脚本里写过这道检查（"没有检索到任何来源"），写探针时没抄过来。
    # **同一条纪律，另一个入口**（BC-49→BC-70→BC-71 的老形态）。
    if len(sources) < 5 or total_chars < 2000:
        print(f"⛔ 输入不足以复现问题（来源 {len(sources)} 条 / 正文 {total_chars} 字）。")
        print("   本次**不产出任何结论**。先查：Milvus 是否在运行、"
              "集合是否存在、kb_scope 是否解析正确。")
        return 2

    wizard = CodeWizard(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    prompt = inv.EXPLORATORY_PROMPT.format(
        subject=params["subject_name"], as_of=params["as_of"],
        sources=inv.format_sources_for_prompt(sources),
        max_findings=inv.MAX_FINDINGS)

    reasons: Counter = Counter()
    rows: List[Dict[str, Any]] = []
    for i in range(1, tries + 1):
        # 单次异常**不得终止整批**：探针存在的意义就是研究间歇失败，
        # 而异常本身就是最值得记的样本。上一版第 8 次抛异常直接死掉，
        # 前 7 次的读数只能从日志里捞。
        try:
            content, meta = await wizard.call_llm(
                system_prompt=("你是尽职调查分析师的助手，只负责从给定材料中"
                               "摘录可溯源的事实。你的产出不进入授信决策，"
                               "不得给出风险判断或结论性评价。"),
                user_prompt=prompt, json_mode=True, temperature=0.2,
                max_tokens=CodeWizard.INVESTIGATION_MAX_TOKENS,
                timeout=CodeWizard.INVESTIGATION_TIMEOUT, return_meta=True,
            )
        except Exception as exc:  # noqa: BLE001
            label = f"异常:{type(exc).__name__}"
            reasons[(False, label)] += 1
            rows.append({"i": i, "ok": False, "finish": label, "chars": 0,
                         "tokens": None, "unbalanced": False, "findings": 0})
            print(f"  #{i}  调用异常｜{type(exc).__name__}: {str(exc)[:110]}")
            continue

        raw = str(content or "").strip()
        parsed = wizard.parse_json_response(content)
        ok = bool(parsed)
        finish = str((meta or {}).get("finish_reason") or "未提供")
        tokens = (meta or {}).get("completion_tokens")
        unbalanced = raw.count("{") > raw.count("}")
        reasons[(ok, finish)] += 1
        rows.append({"i": i, "ok": ok, "finish": finish, "chars": len(raw),
                     "tokens": tokens, "unbalanced": unbalanced,
                     "findings": len((parsed or {}).get("findings") or [])})
        ratio = (len(raw) / tokens) if tokens else None
        print(
            f"  #{i}  {'解析成功' if ok else '解析失败'}"
            f"｜finish={finish}｜{len(raw)} 字"
            f"｜completion_tokens={tokens}"
            + (f"｜{ratio:.2f} 字/token" if ratio else "")
            + f"｜括号{'不平' if unbalanced else '配平'}"
            + f"｜findings={rows[-1]['findings']}"
        )

    print()
    bad = [r for r in rows if not r["ok"]]
    print(f"失败 {len(bad)}/{tries}")
    for (ok, finish), n in reasons.most_common():
        print(f"  {'成功' if ok else '失败'} × finish={finish}：{n} 次")

    if bad:
        chars = [r["chars"] for r in bad]
        toks = [r["tokens"] for r in bad if r["tokens"] is not None]
        print(f"\n失败样本：{min(chars)}–{max(chars)} 字"
              + (f"，completion_tokens {min(toks)}–{max(toks)}" if toks else ""))
        print("判读：finish=length → 上界确实被打满（预算估算需修正）；"
              "finish=stop → 模型自报正常结束却交残缺 JSON，属指令遵循问题。")
    else:
        print("\n本批次未复现。失败率约 13%，样本量不足时复现不到属正常——"
              "**不得据此判定问题已消失**。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="case01", choices=("case01", "mock001", "mock002"))
    parser.add_argument("--tries", type=int, default=8)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    return asyncio.run(_probe(args.case, args.tries, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
