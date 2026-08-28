# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""恢复一个**已经暂停**的复核卡点，并核对终稿与复核人看到的是不是同一份。

## 与 verify_review_persistence.py 的分工

那个脚本自己跑一轮再复核，用于回归；本脚本**不发起新的一轮**，
只恢复一个已存在的暂停会话——因此它同时验证了一件设计约束：

    检查点必须持久化而非 MemorySaver：`DeepResearchV2Service()` 是每次请求
    新建的，进程内内存检查点跨请求必然失效
    —— graph.py 里写在 `_get_graph_checkpointer` 上方的那段注释

服务进程已经整个退出过一次。如果还能从断点继续，那段约束就不只是注释。

## 要回答的问题

复核人在界面上读到的报告，与恢复后真正落盘的终稿，是不是同一份？

界面上的报告来自 `report_draft` 事件，而该事件只由确定性渲染器发出；
`_revise_report` 是一次 LLM 全文重写，它不发这个事件。两者若不一致，
就意味着**复核人签的和最终交付的不是同一份文件**。

用法：cd backend && python eval/resume_paused_review.py <session_id>
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

REVIEWER = "阶段1验证员"
COMMENT = (
    "阶段 1 调查层验证运行：沿用规则引擎判定的高风险结论，不作等级改判。"
    "本次复核仅用于核对终稿与复核界面所见是否一致。"
)


def _events(sse: str) -> list[dict[str, Any]]:
    out = []
    for payload in re.findall(r"^data:\s*(.+)$", sse, flags=re.MULTILINE):
        if payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return out


def _shape(report: str) -> dict[str, Any]:
    """报告的结构指纹。比长度更能说明"少的是哪一部分"。"""
    return {
        "长度": len(report),
        "逐项核查行": report.count("；取证时间："),
        "有结论段": "## 结论" in report,
        "有评级块": "风险评级" in report,
        "有调查层": "调查层补充（不参与授信裁决" in report,
        "有证据附录": "证据溯源附录（由系统生成" in report,
    }


async def main() -> int:
    from service.checkpoint_service import CheckpointService
    from service.deep_research_v2.service import DeepResearchV2Service

    session_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not session_id:
        print("用法：python eval/resume_paused_review.py <session_id>")
        return 2

    checkpoints = CheckpointService()

    # ① 先快照复核前的状态。BC-69 的修复会在恢复后重写检查点，
    #    不先取就永远拿不到"复核人当时看到的是什么"。
    before = checkpoints.load_checkpoint(session_id)
    if before is None:
        print(f"找不到会话 {session_id} 的检查点")
        return 1
    before_report = before.get("final_report") or ""
    print(f"会话 {session_id}")
    print(f"复核前 phase={before.get('phase')} 等级={(before.get('risk_assessment') or {}).get('level')}")
    print(f"复核前报告（= 界面上呈现的那一份）：{json.dumps(_shape(before_report), ensure_ascii=False)}")
    print()

    # ② 恢复断点。服务进程已经退出过一次，能走通即证明检查点确实在库里。
    service = DeepResearchV2Service()
    decision = {"approved": True, "reviewer": REVIEWER, "comment": COMMENT}
    complete: dict[str, Any] | None = None
    resumed = False
    async for sse in service.submit_review(session_id, decision):
        for event in _events(sse):
            kind = event.get("type")
            if kind == "research_resumed":
                resumed = True
            elif kind == "human_review_completed":
                print(f"复核已应用：等级={event.get('level')}")
            elif kind == "research_complete":
                complete = event
            elif kind == "error":
                print(f"恢复失败：{event.get('content')}")
                return 1
    print(f"断点恢复：{'成功' if resumed else '未收到 research_resumed'}")
    if complete is None:
        print("未收到 research_complete —— 恢复没有走到终局")
        return 1

    sse_report = complete.get("final_report") or ""
    print(f"终局事件报告：{json.dumps(_shape(sse_report), ensure_ascii=False)}")
    print()

    # ③ 绕过内存，直接从库里回读终稿
    after = checkpoints.load_checkpoint(session_id)
    after_report = (after or {}).get("final_report") or ""
    print(f"落盘终稿：{json.dumps(_shape(after_report), ensure_ascii=False)}")
    print()

    fail = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fail
        if not ok:
            fail += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))

    review = (after or {}).get("risk_assessment", {}).get("human_review") or {}
    check("复核人已落盘", review.get("reviewer") == REVIEWER, f"实际 {review.get('reviewer')!r}")
    check("复核意见已落盘", COMMENT[:12] in (review.get("comment") or ""))
    check("终局事件与落盘一致", sse_report == after_report,
          f"SSE {len(sse_report)} 字 / 落盘 {len(after_report)} 字")
    check("调查层随终稿保留",
          "调查层补充（不参与授信裁决" in after_report)
    check("证据附录随终稿保留",
          "证据溯源附录（由系统生成" in after_report)

    # 核心问题：复核人看到的逐项核查正文，还在终稿里吗
    before_rows = before_report.count("；取证时间：")
    after_rows = after_report.count("；取证时间：")
    check("逐项核查正文未在修订中丢失", after_rows >= before_rows,
          f"复核前 {before_rows} 行 → 终稿 {after_rows} 行")

    print(f"\n{6 - fail}/6 通过")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
