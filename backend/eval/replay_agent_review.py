"""One focused live Critic -> optional Writer -> Critic replay of a frozen notebook.

Not Graph/HTTP/UI, investigation, rating, or approval E2E. No Scout, database,
ORACLE, retries, or model/budget overrides. Output must be a new directory.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import logging
from pathlib import Path
import subprocess
import sys
from time import monotonic

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "eval/agent_e2e_pack/runs/MEMORY-T01-001/result.json"
sys.path.insert(0, str(ROOT / "app"))


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _capture_calls(agent, calls):
    original = agent.call_llm
    signature = inspect.signature(original)

    async def recorded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        request = dict(bound.arguments)
        row = {"agent": agent.name, "model": agent.model, "request": request,
               "production_time_anchor": agent._with_time_anchor(""),
               "started_at": datetime.now(timezone.utc).isoformat()}
        calls.append(row)
        started = monotonic()
        try:
            response = await original(*args, **kwargs)
            row["response"] = deepcopy(response)
            return response
        except BaseException as exc:
            # Keep actual exception behavior; never archive provider exception text.
            row["exception_type"] = type(exc).__name__
            raise
        finally:
            row["elapsed_seconds"] = round(monotonic() - started, 3)

    agent.call_llm = recorded


async def main(output_dir, input_path=DEFAULT_INPUT):
    input_path = Path(input_path).resolve()
    original_bytes = input_path.read_bytes()
    archived = json.loads(original_bytes.decode("utf-8"))
    notebook = archived.get("agent_investigation")
    if not isinstance(notebook, dict) or not archived.get("query"):
        raise ValueError("expected_frozen_investigator_archive")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    load_dotenv(ROOT / ".env")
    # Production logging may contain provider diagnostics. This replay emits only
    # a compact summary; raw model replies are retained in the new calls archive.
    logging.disable(logging.CRITICAL)
    from config.llm_config import get_config
    from service.deep_research_v2.agents.critic import CriticMaster
    from service.deep_research_v2.agents.writer import LeadWriter
    from service.deep_research_v2.investigation_report import append_investigation_report
    from service.deep_research_v2.review_targets import analysis_revision_issues
    from service.deep_research_v2.state import create_initial_state

    config = get_config()
    checkout = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT.parent,
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT.parent,
                           capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    title = "# 冻结自主调查记录的复核实验"
    state = create_initial_state(archived["query"], "focused-agent-review", search_web=False,
                                 search_local=False, as_of=archived.get("as_of", ""),
                                 subject_name=archived.get("subject_name", ""),
                                 due_diligence=True, investigation=False, research_strategy="agent")
    state.update(agent_investigation=deepcopy(notebook), field_checks=[], risk_assessment={},
                 phase="reviewing", max_iterations=config.research.max_iterations)
    state["final_report"] = append_investigation_report(title, state["agent_investigation"])
    manifest = {
        "input_path": str(input_path), "input_sha256": _sha(original_bytes),
        "input_git_revision": archived.get("git_revision"), "input_model": archived.get("model"),
        "query": archived["query"], "as_of": archived.get("as_of"), "subject_name": archived.get("subject_name"),
        "source_manifest": deepcopy(archived.get("source_manifest", [])),
        "initial_report_sha256": _sha(state["final_report"].encode("utf-8")),
        "git_revision": checkout, "checkout_dirty": bool(dirty),
        "configured_models": {"critic": config.agents.critic.to_dict(), "writer": config.agents.writer.to_dict()},
        "max_iterations": state["max_iterations"], "model_or_budget_overrides": False,
        "test_boundaries": [
            "Synthetic frozen archive only; original input and failure records are never rewritten.",
            "At most two production Critic calls and one committed AI analysis revision; no reruns until pass.",
            "Empty checklist and risk state: no rule rating, credit decision, approval or verification acceptance.",
            "No database, Scout, search, Graph, HTTP/UI or ORACLE invocation.",
            "Node call_llm parameters and raw responses are recorded; production time anchor is separately shown.",
            "Production node parameters remain untouched, including existing Writer-specific limits.",
            "Exit code zero means harness completion, not semantic quality or end-to-end acceptance.",
        ],
    }
    _json(output_dir / "input_manifest.json", manifest)
    calls, rounds = [], []
    result = {"test_type": "focused_live_agent_review_not_e2e", "input_manifest": manifest,
              "started_at": datetime.now(timezone.utc).isoformat(), "state": state, "rounds": rounds,
              "writer_attempted": False, "writer_revision_committed": False}
    started = monotonic()
    try:
        critic = CriticMaster(config.api_key, config.base_url, config.agents.critic.model)
        writer = LeadWriter(config.api_key, config.base_url, config.agents.writer.model)
        for agent in (critic, writer):
            agent.as_of = state.get("as_of", "")
            _capture_calls(agent, calls)

        async def review_once():
            await critic.process(state)
            rounds.append({"phase": state["phase"], "iteration": state["iteration"],
                           "quality_review": deepcopy(state.get("quality_review")),
                           "critic_feedback": deepcopy(state["critic_feedback"]),
                           "report_sha256": _sha(state["final_report"].encode("utf-8"))})

        await review_once()
        if (state.get("phase") == "revising" and not (state.get("quality_review") or {}).get("degraded")
                and analysis_revision_issues(state)):
            result["writer_attempted"] = True
            before = deepcopy(state["agent_investigation"].get("analysis_revisions", []))
            await writer._revise_agent_analysis(state)
            changed = state["agent_investigation"].get("analysis_revisions", []) != before
            result["writer_revision_committed"] = changed
            if changed:
                state["final_report"] = append_investigation_report(title, state["agent_investigation"])
                state["phase"] = "reviewing"
                await review_once()
                result["stop_reason"] = "focused_replay_budget_consumed"
            else:
                result["stop_reason"] = "writer_did_not_commit_revision"
        else:
            result["stop_reason"] = "no_executable_writer_revision_or_review_stopped"
    except Exception as exc:
        result["harness_error_type"] = type(exc).__name__
        result["stop_reason"] = "harness_error"
    finally:
        result.update(finished_at=datetime.now(timezone.utc).isoformat(),
                      elapsed_seconds=round(monotonic() - started, 3), model_call_count=len(calls),
                      input_unchanged=_sha(input_path.read_bytes()) == manifest["input_sha256"])
        _json(output_dir / "calls.json", calls)
        _json(output_dir / "result.json", result)
        (output_dir / "report.md").write_text(state["final_report"], encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "phase": state["phase"],
                      "investigation_status": state["agent_investigation"].get("status"),
                      "review_rounds": len(rounds), "model_calls": len(calls),
                      "writer_revision_committed": result["writer_revision_committed"],
                      "review_degraded": (state.get("quality_review") or {}).get("degraded"),
                      "unresolved_issues": state.get("unresolved_issues"),
                      "stop_reason": result["stop_reason"], "input_unchanged": result["input_unchanged"]},
                     ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Frozen synthetic Investigator result.json")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing archives are refused")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("--output-dir must not already exist")
    try:
        outcome = asyncio.run(main(args.output_dir, args.input))
    except Exception as exc:
        print(json.dumps({"harness_error_type": type(exc).__name__}))
        sys.exit(1)
    sys.exit(1 if outcome.get("harness_error_type") else 0)
