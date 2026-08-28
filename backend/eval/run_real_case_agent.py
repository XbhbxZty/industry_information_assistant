"""Run one sealed real-data case through the multi-agent due-diligence workflow."""

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

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parents[1]
EVAL = Path(__file__).resolve().parent
PROCESSED = EVAL / "real_cases_processed"
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(EVAL))
load_dotenv(BACKEND / ".env")

from real_case_rag import scope_for_case, validate_case_id  # noqa: E402
from retrieval_fixture import RetrievalFixture, install as install_fixture  # noqa: E402


AGENT_NAMES = ("architect", "scout", "data_analyst", "wizard", "critic", "writer")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _subject_name(manifest: dict[str, Any]) -> str:
    subject = manifest.get("subject") or {}
    name = subject.get("name") or subject.get("legal_name")
    if not name:
        raise ValueError("case manifest does not contain a subject name")
    return str(name)


def _build_query(manifest: dict[str, Any]) -> str:
    subject = _subject_name(manifest)
    scenarios = manifest.get("business_scenario") or []
    if isinstance(scenarios, str):
        scenarios = [scenarios]
    scenario_text = "；".join(str(item) for item in scenarios if item) or "企业信用尽职调查"
    business_type = manifest.get("primary_business_type") or "corporate_credit"
    cutoff = manifest.get("research_cutoff") or ""
    return (
        f"请对{subject}开展真实数据尽职调查。业务类型：{business_type}；"
        f"业务场景：{scenario_text}；研究截止日：{cutoff}。"
        "只允许使用本次提供的本地白名单材料，不搜索互联网，不引用截止日后事实，"
        "不得把资料缺失表述为无风险。请输出主体识别、经营与财务、司法合规、"
        "交易相关性、证据缺口、风险等级、是否需要人工复核和建议决策，并为关键结论标注来源。"
    )


def _resolve_agent_models(model: str, overrides: dict[str, str]) -> dict[str, str]:
    """Per-node model matrix.

    ``--model`` used to overwrite **all six** nodes, which is not model routing
    but the abandonment of it (BC-56): production deliberately runs a fast model
    on the high-frequency Scout, a reasoning model on the low-frequency Critic,
    and a general model elsewhere.  Flattening that matrix is what turned one
    slow node into a 915-second run.

    ``--model`` is still accepted as the "same model everywhere" shorthand
    because a single-variable sweep needs it; but per-node overrides now exist so
    an ablation can move exactly one node.  Passing neither keeps the production
    matrix untouched.
    """
    from config.llm_config import get_config

    config = get_config()
    resolved: dict[str, str] = {}
    for name in AGENT_NAMES:
        chosen = overrides.get(name) or model or getattr(config.agents, name).model
        getattr(config.agents, name).model = chosen
        resolved[name] = chosen
    return resolved


async def run_case(
    case_id: str,
    model: str,
    output_root: Path,
    *,
    agent_model_overrides: dict[str, str] | None = None,
    fixture_path: Path | None = None,
    fixture_mode: str = "",
) -> dict[str, Any]:
    case_id = validate_case_id(case_id)
    case_dir = PROCESSED / case_id
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    scope = scope_for_case(case_id)
    query = _build_query(manifest)
    cutoff = str(manifest.get("research_cutoff") or "")

    # The process is dedicated to offline evaluation. Disable the production
    # human-review interrupt so a batch can produce a scoreable draft; the report
    # must still state whether human review is required.
    from config.verification_policy import POLICY
    from service.deep_research_v2.service import DeepResearchV2Service

    agent_models = _resolve_agent_models(model, agent_model_overrides or {})
    POLICY.require_human_review_gate = False

    fixture: RetrievalFixture | None = None
    undo_fixture = None
    if fixture_mode == "replay":
        fixture = RetrievalFixture.load(fixture_path)
    elif fixture_mode == "record":
        fixture = RetrievalFixture({"case_id": case_id})
    if fixture is not None:
        undo_fixture = install_fixture(fixture, fixture_mode)

    session_id = f"real-eval-{case_id}-{uuid.uuid4().hex[:12]}"
    run_dir = output_root / case_id / session_id
    run_dir.mkdir(parents=True, exist_ok=False)
    input_record = {
        "case_id": case_id,
        "session_id": session_id,
        "subject": _subject_name(manifest),
        "query": query,
        "research_cutoff": cutoff,
        "model": model,
        "agent_models": agent_models,
        "search_web": False,
        "search_local": True,
        "kb_scope": scope,
        "human_review_gate": False,
        "max_iterations": 0,
        "reference_layer_read": False,
        "post_cutoff_layer_read": False,
        # 回放运行**不是**一次完整的端到端运行：规划与检索都被冻结了。
        # 必须落在 input.json 里，否则它会被误当成生产链路的成绩（BC-25）。
        "retrieval_fixture": {
            "mode": fixture_mode or "none",
            "path": fixture_path.as_posix() if fixture_path else "",
            "content_sha256": fixture.content_hash() if fixture_mode == "replay" else "",
        },
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(run_dir / "input.json", input_record)

    service = DeepResearchV2Service(max_iterations=1)
    events_path = run_dir / "events.jsonl"
    final_event: dict[str, Any] | None = None
    error_events: list[dict[str, Any]] = []
    try:
        with events_path.open("w", encoding="utf-8") as event_file:
            async for sse in service.research(
                query=query,
                session_id=session_id,
                user_id=None,
                search_web=False,
                search_local=True,
                # Defense in depth for a sealed run: no recursive source tracing or
                # critic-triggered re-research. Scout also enforces search_web=False.
                max_iterations=0,
                subject_name=str((manifest.get("subject") or {}).get("legal_name") or ""),
                business_type=str(manifest.get("primary_business_type") or ""),
                due_diligence=True,
                # 封闭评测强制关闭调查层（计划 9.1）。B 层是一次额外的模型
                # 调用，开着会给每轮评测加上不可复现的时间与费用，
                # 而评测只计 A 层的分。
                investigation=False,
                as_of=cutoff,
                kb_scope=scope,
            ):
                for payload in re.findall(r"^data:\s*(.+)$", sse, flags=re.MULTILINE):
                    if payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        event = {"type": "unparsed_event", "content": payload}
                    event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                    event_file.flush()
                    event_type = event.get("type")
                    print(f"{case_id}: {event_type}", flush=True)
                    if event_type == "research_complete":
                        final_event = event
                    elif event_type == "error":
                        error_events.append(event)
    finally:
        # 打过的补丁必须撤掉：留着会静默影响同进程内后续任何一次运行。
        if undo_fixture is not None:
            undo_fixture()

    fixture_record = dict(input_record["retrieval_fixture"])
    if fixture_mode == "record" and fixture is not None:
        target = fixture_path or (run_dir / "retrieval_fixture.json")
        fixture.save(target)
        fixture_record.update(path=target.as_posix(), **fixture.summary())
    elif fixture_mode == "replay" and fixture is not None:
        fixture_record.update(fixture.summary())

    result = {
        **input_record,
        "retrieval_fixture": fixture_record,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if final_event else "failed",
        "error_events": error_events,
        "final_event": final_event,
    }
    _write_json(run_dir / "result.json", result)
    if final_event:
        (run_dir / "report.md").write_text(
            str(final_event.get("final_report") or ""), encoding="utf-8"
        )
    return {"run_dir": run_dir.as_posix(), **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, dest="case_id")
    parser.add_argument(
        "--model", default="",
        help="同一个模型铺满六个节点（单变量扫描用）。留空则保留生产分节点配置",
    )
    for name in AGENT_NAMES:
        parser.add_argument(
            f"--model-{name.replace('_', '-')}", dest=f"model_{name}", default="",
            help=f"只覆盖 {name} 节点的模型，优先于 --model",
        )
    parser.add_argument("--output-root", type=Path, default=EVAL / "runs" / "real_cases")
    parser.add_argument(
        "--retrieval-fixture", type=Path, default=None,
        help="固定检索输入的落盘路径（record 写入 / replay 读取）",
    )
    parser.add_argument(
        "--fixture-mode", choices=("record", "replay"), default="",
        help="record：正常跑并录下提纲与检索结果；"
             "replay：冻结提纲与检索结果，只让抽取模型变化（BC-56 消融）",
    )
    args = parser.parse_args()

    if args.fixture_mode == "replay" and not args.retrieval_fixture:
        parser.error("--fixture-mode replay 必须同时给 --retrieval-fixture")
    if args.fixture_mode == "replay" and not args.retrieval_fixture.exists():
        parser.error(f"固定输入文件不存在：{args.retrieval_fixture}")

    overrides = {name: getattr(args, f"model_{name}") for name in AGENT_NAMES}
    result = asyncio.run(run_case(
        args.case_id, args.model, args.output_root.resolve(),
        agent_model_overrides={k: v for k, v in overrides.items() if v},
        fixture_path=args.retrieval_fixture.resolve() if args.retrieval_fixture else None,
        fixture_mode=args.fixture_mode,
    ))
    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "run_dir": result["run_dir"],
        "agent_models": result["agent_models"],
        "retrieval_fixture": result["retrieval_fixture"],
        "error_count": len(result["error_events"]),
        "report_length": len((result.get("final_event") or {}).get("final_report") or ""),
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
