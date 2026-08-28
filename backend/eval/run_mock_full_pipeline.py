# Copyright © 2026 XbhbxZty
"""跑一次**完整**尽调：档案过闸 + RAG 补场景字段 + 评级 + 额度 + 报告。

## 为什么不复用 run_real_case_agent.py

那个 runner 的主体、语料和检索范围全部由 `case_01..case_12` 派生
（`validate_case_id` / `scope_for_case` / manifest.json）——那是封闭评测的
护栏，不该为了跑虚构数据把它放宽。这里另起一个入口，只服务虚构主体。

## 这一轮要证明什么

此前所有真实案例都停在「数据不足，无法评级」（核实率 33% < 60% 闸门），
`评级 → 额度 → 报告` 这一段从未被真实运行走通过。虚构档案已离线验证
可达 93.3% 核实率、中风险、额度 144 万；本次验证它在**生产链路**上
同样成立，且 RAG 能把场景 14 项补上来。
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
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

SUBJECT = "云岭恒晟精密机械有限公司"
COLLECTION = "mock_yunling_hengsheng"
KB_ID = "mock-yunling-hengsheng"
# 文档签署日 2026-03-12、档案取证日 2026-08-18，截止日取后者：不制造越界。
AS_OF = "2026-08-18"

QUERY = (
    f"请对{SUBJECT}开展应收账款保理贷前尽职调查。业务类型：factoring；"
    f"研究截止日：{AS_OF}。只允许使用企业档案与本地知识库材料，不搜索互联网。"
    "请输出主体识别、股权与实际控制人、经营与财务、司法合规、关联与担保、"
    "舆情、风险等级、授信额度建议与是否需要人工复核，并为关键结论标注来源。"
)


async def run(output_root: Path) -> dict[str, Any]:
    from config.verification_policy import POLICY
    from service.deep_research_v2.service import DeepResearchV2Service

    # 离线评测专用：关掉生产人工中断以得到可评审的完整草稿。
    # 报告仍必须自行判断"是否需要人工复核"——那是结论，不是流程开关。
    POLICY.require_human_review_gate = False

    session_id = f"mock-full-{uuid.uuid4().hex[:12]}"
    run_dir = output_root / session_id
    run_dir.mkdir(parents=True, exist_ok=False)
    scope = [{"collection": COLLECTION, "kb_id": KB_ID,
              "kb_name": "mock/云岭恒晟", "document_count": 1}]
    (run_dir / "input.json").write_text(json.dumps({
        "session_id": session_id, "subject": SUBJECT, "query": QUERY,
        "as_of": AS_OF, "kb_scope": scope, "search_web": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    service = DeepResearchV2Service(max_iterations=1)
    final_event: dict[str, Any] | None = None
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        async for sse in service.research(
            query=QUERY, session_id=session_id, user_id=None,
            search_web=False, search_local=True, max_iterations=0,
            subject_name=SUBJECT, business_type="factoring",
            due_diligence=True, as_of=AS_OF, kb_scope=scope,
            # 这条流水线是"完整产出"的样例，调查层要跟着一起跑——
            # 它与封闭评测不同，目的就是看最终交付物长什么样。
            investigation=True,
        ):
            for payload in re.findall(r"^data:\s*(.+)$", sse, flags=re.MULTILINE):
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    event = {"type": "unparsed_event", "content": payload}
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"mock: {event.get('type')}", flush=True)
                if event.get("type") == "research_complete":
                    final_event = event

    (run_dir / "result.json").write_text(json.dumps(
        {"session_id": session_id, "status": "completed" if final_event else "failed",
         "final_event": final_event}, ensure_ascii=False, indent=2), encoding="utf-8")
    if final_event:
        (run_dir / "report.md").write_text(
            str(final_event.get("final_report") or ""), encoding="utf-8")
    return {"run_dir": run_dir.as_posix(), "final_event": final_event}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path,
                        default=Path(__file__).resolve().parent / "runs" / "mock_full")
    args = parser.parse_args()
    result = asyncio.run(run(args.output_root.resolve()))
    fe = result.get("final_event") or {}
    comp = fe.get("completeness") or {}
    ra = fe.get("risk_assessment") or {}
    print(json.dumps({
        "run_dir": result["run_dir"],
        "核心必查": f"{comp.get('required_verified')}/{comp.get('required_total')}",
        "场景": (comp.get("scenario") or {}).get("verified"),
        "等级": ra.get("level"),
        "额度建议": bool(ra.get("credit_recommendation") or ra.get("credit_advice")),
        "报告长度": len(fe.get("final_report") or ""),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("final_event") else 1


if __name__ == "__main__":
    raise SystemExit(main())
