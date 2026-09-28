"""Live HTTP acceptance on synthetic T01/QL fixtures; never approves a review.

Requires a separately started real backend targeting the isolated test database.
Each invocation registers a fresh ordinary account and uploads fresh knowledge
bases through the public API. Passwords and tokens exist only in memory. This
is a transport/investigation run, not a claim that content quality passed.

    python eval/run_agent_stage2_e2e.py --scenario both --dry-run
    python eval/run_agent_stage2_e2e.py --scenario both --base-url http://127.0.0.1:8000

No migrations, profile installation, authentication bypass, model overrides,
review approvals, or deletion of existing resources are performed here.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx


BACKEND = Path(__file__).resolve().parents[1]
PACK = BACKEND / "eval" / "agent_e2e_pack"
SCENARIOS = {
    "t01": {
        "subject_name": "海岫精密部件有限公司", "as_of": "2026-02-20",
        "directory": "uploads/base",
        "files": (
            "01_subject_and_request.txt", "02_financial_statements.txt",
            "03_management_explanation.txt", "04_receivable_aging.txt",
            "05_receipts_until_cutoff.txt",
        ),
        "query": "请对海岫精密部件有限公司拟申请的800万元、12个月流动资金贷款开展截至2026年2月20日的尽职调查。只使用我选择的本地材料。重点判断2025年经营现金流转负是否主要由提前备货解释，核查回款改善能否缓释风险。请区分已观察事实、推断、相互矛盾的陈述和仍缺失的信息，给出可追溯依据及按优先级排列的补件清单。不要代替人工审批。",
    },
    "ql": {
        "subject_name": "岐澜精工传动有限公司", "as_of": "2026-05-18",
        "directory": "approve/uploads",
        "files": (
            "01_申请与主体确认.txt", "02_工商登记与股权结构.txt", "03_审计报告摘要.txt",
            "04_司法与征信查询.txt", "05_关联关系与对外担保.txt", "06_舆情与监管处罚.txt",
            "07_经营与中标记录.txt", "08_应收账龄与期后回款.txt",
        ),
        "query": "请对岐澜精工传动有限公司拟申请的1500万元、12个月流动资金贷款开展截至2026年5月18日的尽职调查。只使用我选择的本地材料与企业档案，不要检索互联网。请逐项交代固定二十项核查清单的核实情况，区分已观察事实、推断与仍缺失的信息，说明经营现金流、应收账龄与期后回款之间的勾稽关系，并为关键结论标注可追溯来源。风险等级与授信额度以规则引擎的测算为准，不要在正文里改写等级或金额。",
    },
}
TERMINALS = {"research_complete", "human_review_required", "research_cancelled"}
SENSITIVE_KEYS = {
    "access_token", "refresh_token", "password", "hashed_password", "authorization",
    "cookie", "set-cookie", "api_key", "secret", "signature", "checkpoint_signature",
    "seal", "checkpoint_seal", "hmac", "signing_key",
}
PUBLIC_STATE_KEYS = (
    "research_strategy", "as_of", "subject_name", "company_name", "phase", "iteration",
    "agent_investigation", "quality_review", "critic_feedback", "errors",
    "risk_assessment", "completeness", "rag_evidence_summary", "search_failures",
    "section_failures", "agent_failures",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_base_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in {"", "/"}):
        raise ValueError("Only a plain localhost HTTP origin is allowed")
    return value.rstrip("/")


def fixture_manifest(scenario: str) -> list[dict[str, Any]]:
    spec = SCENARIOS[scenario]
    folder = (PACK / spec["directory"]).resolve()
    manifest = []
    for name in spec["files"]:
        path = folder / name
        if path.resolve().parent != folder or not path.is_file():
            raise ValueError(f"Missing/unsafe synthetic fixture: {name}")
        payload = path.read_bytes()
        if not payload or len(payload) > 256 * 1024:
            raise ValueError(f"Unexpected synthetic fixture size: {name}")
        payload.decode("utf-8-sig")
        manifest.append({"filename": name, "bytes": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest()})
    return manifest


def redact(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if str(k).lower() in SENSITIVE_KEYS
                        or str(k).lower().endswith(("_api_key", "_secret", "_password", "_hmac_key"))
                        else redact(v, secret_values)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED JWT]", value)
        value = re.sub(r"(https?://|postgresql(?:\+\w+)?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    return value


def write_json(path: Path, value: Any, secret_values: tuple[str, ...] = ()) -> None:
    path.write_text(json.dumps(redact(value, secret_values), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checkout_metadata() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=BACKEND.parent, capture_output=True,
                              text=True, encoding="utf-8", timeout=10, check=True).stdout.strip()
    try:
        return {"runner_checkout_commit": git("rev-parse", "HEAD"),
                "runner_checkout_dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"runner_checkout_commit": None, "runner_checkout_dirty": None}


async def sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Parse actual SSE framing, including comments and multiline data."""
    data: list[str] = []
    size = 0
    async for line in lines:
        if line == "":
            if data:
                text = "\n".join(data)
                if text != "[DONE]":
                    event = json.loads(text)
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise ValueError("Malformed research event")
                    yield event
                data, size = [], 0
        elif line.startswith("data:"):
            part = line[5:]
            if part.startswith(" "):
                part = part[1:]
            size += len(part)
            if size > 4_000_000:
                raise ValueError("SSE event exceeds the archive size limit")
            data.append(part)
    if data and "\n".join(data) != "[DONE]":
        raise ValueError("SSE ended inside an event")


async def request_json(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    if response.is_error:
        # Do not print error bodies: validation responses can echo passwords.
        raise RuntimeError(f"HTTP {response.status_code} at {method} {path}")
    return response.json()


async def register_test_account(client: httpx.AsyncClient, run_id: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    username = "stage2_" + run_id[-12:]
    password = secrets.token_urlsafe(30)
    registration = await request_json(client, "POST", "/auth/register", json={
        "username": username, "email": username + "@example.com", "password": password,
    })
    account = registration.get("user") or {}
    if not account.get("id") or account.get("is_superuser") is not False:
        raise RuntimeError("Registration did not create an ordinary test account")
    # Exercise the login endpoint instead of silently reusing registration's token.
    login = await request_json(client, "POST", "/auth/login", json={"username": username, "password": password})
    token = login.get("access_token")
    if not isinstance(token, str) or not token or (login.get("user") or {}).get("id") != account["id"]:
        raise RuntimeError("Login returned an invalid test identity")
    client.headers["Authorization"] = "Bearer " + token
    me = await request_json(client, "GET", "/auth/me")
    if me.get("id") != account["id"] or me.get("is_active") is not True:
        raise RuntimeError("Authenticated identity is not the newly created account")
    return {"id": account["id"], "alias": "fresh_stage2_test_account", "is_superuser": False}, (
        password, token, str(registration.get("access_token") or ""),
    )


async def prepare_kb(client: httpx.AsyncClient, scenario: str, run_id: str,
                     result: dict[str, Any], index_timeout: int) -> None:
    manifest = fixture_manifest(scenario)
    result["source_manifest"] = manifest
    kb = await request_json(client, "POST", "/knowledge-bases", json={
        "name": f"E2E-STAGE2-{scenario.upper()}-{run_id}",
        "description": "Synthetic acceptance fixtures only; not a real credit decision.",
    })
    result["kb_id"], result["kb_name"] = str(kb["id"]), str(kb["name"])
    documents = {}
    async with asyncio.timeout(index_timeout):
        for entry in manifest:
            path = PACK / SCENARIOS[scenario]["directory"] / entry["filename"]
            with path.open("rb") as source:
                uploaded = await request_json(client, "POST", f"/knowledge-bases/{kb['id']}/documents",
                                              files={"file": (path.name, source, "text/plain")})
            documents[str(uploaded["id"])] = entry["filename"]
            result["uploaded_documents"] = [{"id": key, "filename": value} for key, value in documents.items()]
        while True:
            rows = await request_json(client, "GET", f"/knowledge-bases/{kb['id']}/documents")
            if not isinstance(rows, list) or {str(row["id"]) for row in rows} != set(documents):
                raise RuntimeError("Knowledge-base document identities differ from this run's uploads")
            result["indexed_documents"] = [{k: row.get(k) for k in ("id", "filename", "status", "chunk_count")} for row in rows]
            if any(row.get("status") == "failed" for row in rows):
                raise RuntimeError("A synthetic document failed parsing/indexing")
            if all(row.get("status") == "completed" and int(row.get("chunk_count") or 0) > 0 for row in rows):
                break
            await asyncio.sleep(2)
        # A completed SQL record alone is not proof that vectors can be read.
        checks = []
        for doc_id, filename in documents.items():
            chunks = await request_json(client, "GET", f"/knowledge-bases/{kb['id']}/documents/{doc_id}/chunks")
            contents = chunks.get("chunks") or []
            if not contents or any(not str(chunk.get("content") or "").strip() for chunk in contents):
                raise RuntimeError("Indexed synthetic document has no readable vector chunks")
            checks.append({"document_id": doc_id, "filename": filename, "readable_chunks": len(contents)})
        result["index_readback"] = checks


async def capture_checkpoint(client: httpx.AsyncClient, session_id: str,
                             secret_values: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    payload = await request_json(client, "GET", f"/research/checkpoint/{session_id}/full")
    checkpoint = payload.get("checkpoint") or {}
    state = checkpoint.get("state_json") or {}
    ui = checkpoint.get("ui_state_json") or {}
    # Never archive signed checkpoint internals or arbitrary identity/config state.
    selected = {key: state[key] for key in PUBLIC_STATE_KEYS if key in state}
    notebook = selected.get("agent_investigation")
    if isinstance(notebook, dict):
        selected["agent_investigation"] = {key: value for key, value in notebook.items() if key != "sources"}
    summary = {"found": payload.get("success") is True, "status": checkpoint.get("status"),
               "phase": checkpoint.get("phase"), "state": selected,
               "research_outcome": ui.get("research_outcome"),
               "kb_scope": [{"kb_id": entry.get("kb_id"), "kb_name": entry.get("kb_name")}
                            for entry in state.get("kb_scope", [])]}
    return redact(summary, secret_values), str(checkpoint.get("final_report") or state.get("final_report") or "")


async def run_scenario(client: httpx.AsyncClient, scenario: str, run_id: str, output: Path,
                       secret_values: tuple[str, ...], *, case_timeout: int, index_timeout: int,
                       max_iterations: int) -> dict[str, Any]:
    folder = output / scenario
    folder.mkdir()
    session_id = f"stage2-{scenario}-{uuid.uuid4().hex}"
    result: dict[str, Any] = {"scenario": scenario, "session_id": session_id, "started_at": utc_now(),
                              "pipeline_pass": False, "quality_verdict": "not_scored",
                              "approvals_submitted": False, "model_calls_stubbed": False,
                              "terminal_status": "not_started", "event_counts": {}}
    started = time.monotonic()
    stage = "upload_and_index"
    launched = False
    report = ""
    try:
        await prepare_kb(client, scenario, run_id, result, index_timeout)
        spec = SCENARIOS[scenario]
        request = {"query": spec["query"], "session_id": session_id, "kb_name": result["kb_name"],
                   "search_modes": ["local"], "version": "v2", "as_of": spec["as_of"],
                   "subject_name": spec["subject_name"], "due_diligence": True,
                   "research_strategy": "agent", "max_iterations": max_iterations}
        result["request"] = request
        result["profile_note"] = "No profile supplied/installed by runner. Server may match its existing companies.json; archive the actual result, not an assumed 15/15."
        write_json(folder / "input.json", result, secret_values)
        stage = "research_stream"
        launched = True
        async with asyncio.timeout(case_timeout):
            async with client.stream("POST", "/research/stream", json=request,
                                     timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10)) as response:
                if response.status_code != 200 or "text/event-stream" not in response.headers.get("content-type", ""):
                    raise RuntimeError(f"Research did not return SSE (HTTP {response.status_code})")
                result["stream_http_status"] = response.status_code
                with (folder / "events.jsonl").open("w", encoding="utf-8") as events:
                    async for event in sse_events(response.aiter_lines()):
                        safe_event = redact(event, secret_values)
                        events.write(json.dumps(safe_event, ensure_ascii=False) + "\n")
                        events.flush()
                        kind = event["type"]
                        result["event_counts"][kind] = result["event_counts"].get(kind, 0) + 1
                        print(f"{scenario}: {kind}", flush=True)
                        if kind == "report_draft":
                            content = event.get("content")
                            report = content.get("content", "") if isinstance(content, dict) else str(content or "")
                        if kind in TERMINALS:
                            result["terminal_event"] = safe_event
                            result["terminal_status"] = {"human_review_required": "awaiting_review",
                                                          "research_complete": "completed",
                                                          "research_cancelled": "cancelled"}[kind]
                            report = str(event.get("final_report") or report)
                            break
        if result["terminal_status"] == "not_started":
            raise RuntimeError("SSE ended without a legal terminal event")
        stage = "checkpoint_readback"
        checkpoint, persisted_report = await capture_checkpoint(client, session_id, secret_values)
        write_json(folder / "checkpoint-summary.json", checkpoint, secret_values)
        result["checkpoint_status"] = checkpoint.get("status")
        result["research_outcome"] = checkpoint.get("research_outcome") or (result.get("terminal_event") or {}).get("research_outcome")
        report = persisted_report or report
        expected = "paused" if result["terminal_status"] == "awaiting_review" else "completed"
        result["pipeline_pass"] = bool(result["terminal_status"] in {"awaiting_review", "completed"}
                                       and checkpoint.get("found") and checkpoint.get("status") == expected
                                       and {entry.get("kb_id") for entry in checkpoint.get("kb_scope", [])} == {result["kb_id"]}
                                       and report.strip() and isinstance(result.get("research_outcome"), dict))
        if not result["pipeline_pass"]:
            result["failure"] = "Terminal event/checkpoint/report/scope contract did not all hold"
    except Exception as exc:
        result["failure"] = redact(f"{type(exc).__name__}: {exc}", secret_values)
        result["failed_stage"] = stage
        result["terminal_status"] = "timed_out" if isinstance(exc, TimeoutError) else "failed"
        if launched:
            try:
                cancel = await request_json(client, "POST", f"/research/cancel/{session_id}", timeout=10)
                result["cancellation_requested"] = cancel.get("success") is True
                result["cancellation_note"] = "A cancellation request is not proof that an in-flight model call stopped."
            except Exception as cancel_error:
                result["cancellation_requested"] = False
                result["cancellation_error"] = type(cancel_error).__name__
    finally:
        result["finished_at"] = utc_now()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if report:
            (folder / "report.md").write_text(redact(report, secret_values), encoding="utf-8")
        write_json(folder / "result.json", result, secret_values)
    return result


async def run(args: argparse.Namespace) -> int:
    base_url = local_base_url(args.base_url)
    cases = ("t01", "ql") if args.scenario == "both" else (args.scenario,)
    manifests = {case: fixture_manifest(case) for case in cases}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "base_url": base_url, "fixtures": manifests,
                          "network_calls": 0, "database_writes": 0}, ensure_ascii=False, indent=2))
        return 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    output = args.output_dir.resolve() if args.output_dir else PACK / "runs" / ("stage2-" + run_id)
    # Refuse to reuse an archive: never overwrite a previous experiment.
    output.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {"run_id": run_id, "started_at": utc_now(), "base_url": base_url,
                               **checkout_metadata(), "server_revision_declared_by_operator": args.server_revision or None,
                               "version_note": "Runner checkout is not proof of server revision; operator must start/restart the server from this checkout.",
                               "scope": "new synthetic account and knowledge bases only", "cases": [],
                               "limits": {"research_seconds_per_case": args.case_timeout,
                                          "upload_index_seconds_per_case": args.index_timeout,
                                          "max_iterations": args.max_iterations}}
    secret_values: tuple[str, ...] = ()
    try:
        # Local HTTP must not be routed through a desktop/system proxy.
        async with httpx.AsyncClient(base_url=base_url, timeout=30, trust_env=False,
                                     follow_redirects=False) as client:
            await request_json(client, "GET", "/hello")
            # Demonstrate the same entry is not anonymously accessible.
            unauthenticated = await client.get("/knowledge-bases")
            if unauthenticated.status_code != 401:
                raise RuntimeError("Knowledge-base endpoint did not reject unauthenticated access")
            summary["unauthenticated_http_status"] = unauthenticated.status_code
            account, secret_values = await register_test_account(client, run_id)
            summary["account"] = account
            for case in cases:
                case_result = await run_scenario(client, case, run_id, output, secret_values,
                                                case_timeout=args.case_timeout, index_timeout=args.index_timeout,
                                                max_iterations=args.max_iterations)
                summary["cases"].append({key: case_result.get(key) for key in (
                    "scenario", "session_id", "kb_id", "pipeline_pass", "quality_verdict",
                    "terminal_status", "research_outcome", "elapsed_seconds", "failure")})
                write_json(output / "summary.json", summary, secret_values)
                if not case_result["pipeline_pass"]:
                    break  # No unbounded retries or second model run after a failed first run.
    except Exception as exc:
        summary["failure"] = redact(f"{type(exc).__name__}: {exc}", secret_values)
    finally:
        summary["finished_at"] = utc_now()
        summary["pipeline_pass"] = len(summary["cases"]) == len(cases) and all(case["pipeline_pass"] for case in summary["cases"])
        summary["retention_note"] = "Fresh synthetic account/KB/checkpoints remain for audit; no existing data was removed. Credentials were kept only in process memory."
        write_json(output / "summary.json", summary, secret_values)
    print(json.dumps({"output_dir": str(output), "pipeline_pass": summary["pipeline_pass"],
                      "quality_verdict": "not_scored", "failure": summary.get("failure")}, ensure_ascii=False), flush=True)
    return 0 if summary["pipeline_pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("t01", "ql", "both"), default="both")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--server-revision", default="", help="Operator-declared revision of the freshly started backend")
    parser.add_argument("--case-timeout", type=int, default=900)
    parser.add_argument("--index-timeout", type=int, default=300)
    parser.add_argument("--max-iterations", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="Validate only synthetic input files; no HTTP/model/database activity")
    args = parser.parse_args()
    if not 30 <= args.case_timeout <= 900 or not 10 <= args.index_timeout <= 600 or not 0 <= args.max_iterations <= 3:
        parser.error("Limits: case timeout 30..900 s, index timeout 10..600 s, iterations 0..3")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
