# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""跑一次**非尽调**的普通研究请求

## 为什么这条路径必须单独跑

尽调模式下 LeadWriter **根本不调模型**（BC-71 改成确定性渲染，
实测节点耗时 0 秒）。也就是说：

    尽调跑了几十轮全绿 ≠ 普通研究能跑

整合报告、章节撰写、代码执行画图——这几条只有非尽调路径会走到。
**同一个缺陷，两条路径，只有一条被跑过**，是这个项目反复出现的形态。

## 本脚本查过的两件事

**一、`max_tokens` 越界（2026-08-23，结论：不成立）**

原本担心 `writer.py` 三处硬写的 `max_tokens=16000` 会 400，
依据是实测的 `Range of max_tokens should be [1, 8192]`。

⚠️ **那个 8,192 是 qwen-max 的**，而 writer 走 `deepseek-v4-flash`
（实测输出上限 ≥65,536）。首跑实测：0 次 400、0 次夹住、
LeadWriter 真调了模型并整合成功。**这条担心是 BC-78 那个误判的延续**
——我把探针误用模型量到的限制，当成了全局的。

`base.MODEL_OUTPUT_CEILING` 那道夹子仍然保留：它现在是一道防御
（有人把 writer 切到 qwen-max 时会夹住并告警），不是一个修复。

**二、代码执行画图（2026-08-24）**

首跑实测 `Code execution error: No module named 'matplotlib'`，
`code` 事件 3 条、`code_fix` 3 条、`code_result` 只有 1 条——重试三次全败。
matplotlib 本来就在 `requirements.txt` 第 58 行，是**环境缺口不是声明缺口**。
装好之后重跑，看那三次重试是否消失、`charts_count` 是否非零。

用法：
    python eval/run_plain_research.py --query "..." --kb mock002
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

OUTPUT_ROOT = BACKEND / "eval" / "runs" / "plain"

#: 复用观察期的知识库配置——不另建一个，否则测的是另一套检索。
SCOPES = {
    "mock002": [{"collection": "mock_yunling_hengsheng",
                 "kb_id": "mock-yunling-hengsheng",
                 "kb_name": "mock/云岭恒晟", "document_count": 1}],
    "mock001": [{"collection": "mock_tairui_dd", "kb_id": "mock-tairui-dd",
                 "kb_name": "mock/泰锐", "document_count": 1}],
}

DEFAULT_QUERY = (
    "请分析云岭恒晟精密机械有限公司所处的精密机械制造行业的发展现状，"
    "包括市场格局、技术趋势与主要风险，并给出一份结构化的行业研究简报。"
)


async def main_async(query: str, kb: str, iterations: int) -> int:
    from config.verification_policy import POLICY
    from service.deep_research_v2.service import DeepResearchV2Service

    # 评测专用：关掉生产人工中断，否则每次都停在卡点等人，拿不到终局事件。
    # 上一次跑就是这样——Critic 判 major_issues（质量分 4.0 < 通过线 7.0），
    # 流程按设计暂停。那不是故障，但它挡住了本脚本要验的东西。
    # 报告仍会照常判「是否需人工复核」——关掉的是流程阻塞，不是结论。
    POLICY.require_human_review_gate = False

    session_id = f"plain-{uuid.uuid4().hex[:8]}"
    run_dir = OUTPUT_ROOT / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"会话 {session_id}")
    print(f"知识库 {kb}｜迭代 {iterations}｜**非尽调模式**")
    print()

    service = DeepResearchV2Service(max_iterations=max(1, iterations))
    final_event: Dict[str, Any] | None = None
    events: List[Dict[str, Any]] = []

    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        async for sse in service.research(
            query=query, session_id=session_id, user_id=None,
            search_web=False, search_local=True, max_iterations=iterations,
            # ⚠️ 这一行是本脚本存在的全部理由。
            due_diligence=False,
            kb_scope=SCOPES.get(kb, []),
        ):
            for payload in re.findall(r"^data:\s*(.+)$", sse, flags=re.MULTILINE):
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                events.append(event)
                if event.get("type") == "research_complete":
                    final_event = event
                if event.get("type") in ("error", "warning"):
                    print(f"  [{event.get('type')}] "
                          f"{str(event.get('content') or event)[:160]}")

    (run_dir / "result.json").write_text(json.dumps(
        {"session_id": session_id, "query": query, "mode": "plain_research",
         "started_at": datetime.now(timezone.utc).isoformat(),
         "status": "completed" if final_event else "failed",
         "final_event": final_event}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print()
    if not final_event:
        print("⛔ 没有拿到终局事件——这条路径没跑通。")
        print(f"   事件 {len(events)} 条，逐条看 {run_dir / 'events.jsonl'}")
        return 1

    report = str(final_event.get("final_report") or "")
    print(f"✅ 跑通｜事件 {len(events)} 条｜报告 {len(report)} 字")
    print(f"   迭代 {final_event.get('iterations')}｜"
          f"事实 {final_event.get('facts_count')}｜"
          f"图表 {final_event.get('charts_count')}｜"
          f"引用 {len(final_event.get('references') or [])}")
    print(f"   产物 {run_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--kb", default="mock002", choices=sorted(SCOPES))
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args()
    return asyncio.run(main_async(args.query, args.kb, args.iterations))


if __name__ == "__main__":
    raise SystemExit(main())
