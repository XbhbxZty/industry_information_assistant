# Copyright © 2026 XbhbxZty
"""BC-69 的端到端验收：复核结论必须真的落进数据库。

## 为什么不能复用 run_mock_full_pipeline.py

那个 runner 关掉了人工复核卡点（`require_human_review_gate = False`）以便
拿到可评分草稿，因此**根本走不到复核路径**——而 BC-69 恰恰只在这条路径上出现。
验收一个只在人工复核后才发生的缺陷，装置必须让复核真的发生。

## 本脚本做三件事

1. 开着复核卡点跑一轮，等它暂停在 `human_review_required`
2. 用程序提交一份**带等级覆盖**的复核结论（覆盖是权限最高的操作，
   也是审计上最需要留痕的那一种）
3. **绕过内存状态、直接读数据库**，逐项比对 SSE 与持久层

第 3 步是重点。BC-69 的教训正是"SSE 推送成功不等于落盘成功"，
所以验收必须从库里读，不能拿返回值自证。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

SUBJECT = "云岭恒晟精密机械有限公司"
REVIEWER = "bc69-verifier"
OVERRIDE_LEVEL = "低风险"
COMMENT = "BC-69 端到端验收：确认日期问题后覆盖等级"


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


async def main() -> int:
    from config.verification_policy import POLICY
    from service.deep_research_v2.service import DeepResearchV2Service

    # 与生产一致：复核卡点开着。这正是被验收的那条路径。
    POLICY.require_human_review_gate = True

    session_id = f"bc69-{uuid.uuid4().hex[:10]}"
    scope = [{"collection": "kb_52c963fd1e254dff98d360a15bbdef48",
              "kb_id": "52c963fd-1e25-4dff-98d3-60a15bbdef48",
              "kb_name": "云岭恒晟精密机械有限公司", "document_count": 1}]
    query = (f"请对{SUBJECT}开展应收账款保理贷前尽职调查，授信 5000 万元。"
             "业务类型：factoring。只使用企业档案与本地知识库材料。")

    print(f"[1/3] 发起尽调 session={session_id}（复核卡点开启）")
    service = DeepResearchV2Service(max_iterations=1)
    paused = None
    async for sse in service.research(
        query=query, session_id=session_id, user_id=None,
        search_web=False, search_local=True, max_iterations=0,
        subject_name=SUBJECT, business_type="factoring",
        due_diligence=True, as_of="2026-08-18", kb_scope=scope,
        # 本脚本验的是复核结论落盘，与调查层无关；关掉省一次模型调用。
        investigation=False,
    ):
        for ev in _events(sse):
            if ev.get("type") == "human_review_required":
                paused = ev
            elif ev.get("type") == "research_complete":
                print("  ⚠️ 直接完成了，没有触发复核卡点——本轮无法验收 BC-69")
                return 2

    if not paused:
        print("  ⚠️ 没有收到 human_review_required，无法验收")
        return 2
    print(f"  已暂停。机器判定等级={paused.get('level')}")
    machine_level = paused.get("level")

    print(f"[2/3] 提交复核结论：覆盖等级 {machine_level} → {OVERRIDE_LEVEL}")
    decision = {"approved": True, "reviewer": REVIEWER,
                "override_level": OVERRIDE_LEVEL, "comment": COMMENT}
    sse_level = sse_report_len = None
    async for sse in service.submit_review(session_id, decision, user_id=None):
        for ev in _events(sse):
            if ev.get("type") == "research_complete":
                sse_level = (ev.get("risk_assessment") or {}).get("level")
                sse_report_len = len(ev.get("final_report") or "")
    print(f"  SSE 终局：等级={sse_level}，报告={sse_report_len} 字")

    # ---- 关键一步：绕过内存，直接读库 ----
    print("[3/3] 从数据库回读（不使用任何内存中的返回值）")
    from core.database import engine
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(text(
            "select phase, status, length(final_report), state_json "
            "from research_checkpoints where session_id = :s"
        ), {"s": session_id}).fetchone()

    if not row:
        print("  ✗ 数据库里没有这个 session 的检查点")
        return 1

    phase, status, db_report_len, state_json = row
    st = state_json if isinstance(state_json, dict) else json.loads(state_json)
    ra = st.get("risk_assessment") or {}
    hr = ra.get("human_review") or {}
    adv = ra.get("credit_recommendation") or ra.get("credit_advice") or {}

    checks = [
        ("等级已落盘且为覆盖后的值", ra.get("level"), OVERRIDE_LEVEL),
        ("等级与 SSE 一致", ra.get("level"), sse_level),
        ("复核人已落盘", hr.get("reviewer"), REVIEWER),
        ("复核意见已落盘", hr.get("comment"), COMMENT),
        ("终稿长度与 SSE 一致", db_report_len, sse_report_len),
        ("phase 与 status 不矛盾", phase, "completed"),
        ("status 为 completed", status, "completed"),
        ("额度载明依据等级", adv.get("based_on_level"), OVERRIDE_LEVEL),
    ]
    print()
    failed = 0
    for label, actual, expected in checks:
        ok = actual == expected
        failed += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}"
              + ("" if ok else f"  (期望 {expected!r})"))

    if hr.get("original_level"):
        print(f"  INFO 机器原始等级留痕: {hr.get('original_level')}（机器判定 {machine_level}）")
    else:
        print("  INFO 未单独保留机器原始等级字段")

    print(f"\n{len(checks) - failed}/{len(checks)} 项通过 —— "
          + ("BC-69 端到端验收通过" if not failed else "仍有未落盘项"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
