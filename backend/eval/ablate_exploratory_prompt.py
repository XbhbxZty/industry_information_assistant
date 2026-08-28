# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""探索性抽取提示词消融 —— 隔离「0 条发现」的成因

## 为什么需要这个装置

阶段 2 冒烟两轮，两个主体都交了 **0 条发现**，而 BC-73 修复前同一个主体
交过 6 条。留痕里**没有拒绝记录**，说明不是准入判据拦的——模型直接交了空数组。

两个候选成因，外观完全相同：

1. **语料里确实没有清单以外的东西**（mock 语料只有 2420 字，
   产能/行业地位/市场份额/出口/研发/订单/竞争 全部 0 次命中）
2. **BC-73 改的提示词措辞把模型吓住了**——「写了不会进报告，
   只会浪费你的输出」加上新增的三个 schema 字段

case_01 有 430 片真实语料，成因 1 对它不成立，所以两个零**必须分开查**。

## 为什么不是再跑几轮流水线

跑一轮完整流水线要几分钟、十几次模型调用，而这里要问的只是
**同一份材料喂给同一个模型，换个提示词会不会有输出**。
隔离成一次调用，既便宜又能真正归因（BC-63「先补仪表，再动手」）。

用法：
    python eval/ablate_exploratory_prompt.py --case case01
    python eval/ablate_exploratory_prompt.py --case mock002 --queries 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

#: BC-73 之前的措辞。**逐字保留**，改一个字这次消融就失去对照意义。
PROMPT_BEFORE_BC73 = """3. **不要重复清单字段。** 营收、净利润、资产负债率、涉诉、担保这些
   已由清单处理，写了也会被丢弃。"""

#: BC-73 之后的措辞（当前生产用的）。
PROMPT_AFTER_BC73 = """3. **不要重复清单字段。** 工商登记、股东、营收、净利润、资产负债率、
   涉诉、担保这些已由清单处理——**系统会自动丢弃这类条目**，
   写了不会进报告，只会浪费你的输出。"""


async def _collect_sources(scope: List[Dict[str, Any]], queries: List[str],
                           limit: int) -> List[Dict[str, Any]]:
    """按 Scout 的方式取一批本地检索结果，作为消融的共同输入。"""
    from service.deep_research_v2.agents.scout import DeepScout

    # search_api_key 给空串：本次只走本地检索，联网路径不会被触碰
    scout = DeepScout(
        llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
        llm_base_url=os.getenv("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        search_api_key="")
    seen, sources = set(), []
    for query in queries:
        outcome = await scout._execute_local_search(query, kb_scope=scope)
        for result in outcome.results:
            key = (result.get("title"), result.get("chunk_index"))
            if key in seen:
                continue
            seen.add(key)
            sources.append({
                "title": str(result.get("title") or "")[:160],
                "url": str(result.get("url") or ""),
                "site_name": str(result.get("site_name") or "本地知识库"),
                "doc_name": str(result.get("doc_name") or ""),
                "date": str(result.get("date") or ""),
                "retrieved_at": "2026-08-23T00:00:00",
                "summary": str(result.get("summary") or result.get("snippet") or "")[:1200],
            })
            if len(sources) >= limit:
                return sources
    return sources


async def _ask(prompt_rule: str, subject: str, as_of: str,
               sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """用指定的规则 3 措辞跑一次抽取，返回原始输出与计数。"""
    from service import investigation_layer as inv
    from service.deep_research_v2.agents.wizard import CodeWizard

    template = inv.EXPLORATORY_PROMPT.replace(PROMPT_AFTER_BC73, prompt_rule)
    assert prompt_rule in template, "措辞替换失败——提示词可能已改动"

    wizard = CodeWizard(llm_api_key=os.getenv("DASHSCOPE_API_KEY", "x"),
                        llm_base_url=os.getenv("LLM_BASE_URL",
                                               "https://dashscope.aliyuncs.com"
                                               "/compatible-mode/v1"))
    response = await wizard.call_llm(
        system_prompt=("你是尽职调查分析师的助手，只负责从给定材料中摘录可溯源的事实。"
                       "你的产出不进入授信决策，不得给出风险判断或结论性评价。"),
        user_prompt=template.format(subject=subject, as_of=as_of,
                                    sources=inv.format_sources_for_prompt(sources),
                                    max_findings=inv.MAX_FINDINGS),
        json_mode=True, temperature=0.2, max_tokens=8000, timeout=180.0,
    )
    payload = wizard.parse_json_response(response) or {}
    return {
        "raw_len": len(response or ""),
        "findings_raw": len(payload.get("findings") or []),
        "relations_raw": len(payload.get("relations") or []),
        "metrics_raw": len(payload.get("metrics") or []),
        "claims": [str(f.get("claim") or "")[:70]
                   for f in (payload.get("findings") or [])[:6]],
    }


async def main_async(case: str, queries: int, limit: int) -> int:
    from run_stage2_observation import SUBJECTS, _resolve

    entry = SUBJECTS[case]
    params = _resolve(entry)
    subject = params["subject_name"]

    probes = ["主要客户 供应商 集中度", "产能 在建工程 产量 项目进展",
              "行业地位 市场份额 竞争格局", "研发投入 专利 技术",
              "经营情况 主营业务 收入构成"][:queries]
    sources = await _collect_sources(params["scope"], probes, limit)
    print(f"主体：{subject}")
    print(f"检索到 {len(sources)} 条来源（{len(probes)} 个探针查询）")
    if not sources:
        print("⚠️ 没有检索到任何来源——0 条发现的成因在检索，不在提示词")
        return 1
    total_chars = sum(len(s["summary"]) for s in sources)
    print(f"送入模型的正文合计 {total_chars} 字\n")

    for label, rule in (("BC-73 之前", PROMPT_BEFORE_BC73),
                        ("BC-73 之后（当前）", PROMPT_AFTER_BC73)):
        out = await _ask(rule, subject, params["as_of"], sources)
        print(f"=== {label}")
        print(f"    findings={out['findings_raw']} relations={out['relations_raw']} "
              f"metrics={out['metrics_raw']}（响应 {out['raw_len']} 字）")
        for claim in out["claims"]:
            print(f"      · {claim}")
        print()

    print("判读：两边都是 0 → 成因在语料或检索；只有「之后」是 0 → 成因在措辞。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="case01", choices=("case01", "mock002"))
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    return asyncio.run(main_async(args.case, args.queries, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
