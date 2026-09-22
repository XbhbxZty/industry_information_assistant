"""Read-only inspection of the existing synthetic T01 account/KB checkpoints.

Never starts research, submits approval, changes state, or writes files.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session")
    args = parser.parse_args()
    baseline = json.loads((ROOT / "eval/agent_e2e_pack/runs/T01-agent-002/result.json").read_text(encoding="utf-8"))
    load_dotenv(ROOT / ".env")
    from core.database_url import resolve_database_urls
    url = urlsplit(resolve_database_urls().psycopg_conninfo)
    if url.hostname not in ("localhost", "127.0.0.1") or url.query:
        raise RuntimeError("Only local test DB inspection is allowed")
    target = urlunsplit(url._replace(path="/industry_assistant_codex"))
    with psycopg.connect(target) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        original = conn.execute("SELECT user_id FROM research_checkpoints WHERE session_id=%s",
                                (baseline["session_id"],)).fetchone()
        if not original:
            raise RuntimeError("Baseline owner unavailable")
        rows = conn.execute("""SELECT session_id,status,phase,iteration,created_at,updated_at,state_json,final_report
            FROM research_checkpoints WHERE user_id=%s AND query=%s
            ORDER BY created_at DESC LIMIT 12""", (original[0], baseline["query"])).fetchall()
        for sid, status, phase, iteration, created, updated, state, report in rows:
            if args.session and sid != args.session:
                continue
            if {str(k.get("kb_id")) for k in state.get("kb_scope", [])} != {baseline["kb_id"]}:
                continue
            result = {"session_id": sid, "status": status, "phase": phase, "iteration": iteration,
                      "created_at": str(created), "updated_at": str(updated), "database": "industry_assistant_codex"}
            if args.session:
                result.update({k: state.get(k) for k in ("query", "as_of", "subject_name", "company_name", "kb_scope",
                    "research_strategy", "search_web", "search_local", "agent_investigation", "field_checks",
                    "critic_feedback", "errors", "risk_assessment", "quality_score", "rag_evidence_summary",
                    "investigation_findings", "investigation_hypotheses", "messages")})
                result["final_report"] = report or state.get("final_report")
            print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
