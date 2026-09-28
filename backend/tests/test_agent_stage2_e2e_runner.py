"""The live acceptance harness itself must not invent success or leak tokens."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


SPEC = importlib.util.spec_from_file_location(
    "stage2_e2e_runner", Path(__file__).resolve().parents[1] / "eval" / "run_agent_stage2_e2e.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("value", [
    "https://example.com", "http://localhost@external.example", "http://127.0.0.1/?target=remote",
    "http://user:password@localhost:8000", "http://localhost:8000/api", "http://localhost:8000/#secret",
])
def test_refuses_nonlocal_or_credentialed_origin(value):
    with pytest.raises(ValueError):
        runner.local_base_url(value)


def test_local_origin_and_fixture_allowlist():
    assert runner.local_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert runner.local_base_url("http://[::1]:8000") == "http://[::1]:8000"
    assert len(runner.fixture_manifest("t01")) == 5
    # QL is an existing user-supplied, untracked optional fixture package.
    if (runner.PACK / "approve/uploads").exists():
        assert len(runner.fixture_manifest("ql")) == 8


def test_recursively_redacts_credentials_without_dropping_usage_counts():
    payload = {"access_token": "known-token", "usage": {"completion_tokens": 5},
               "signature": "sealed", "children": [{"api_key": "secret"}],
               "text": "Bearer arbitrary_token known-password postgresql://user:password@localhost/db"}
    redacted = runner.redact(payload, ("known-token", "known-password"))
    serialized = json.dumps(redacted)
    for secret in ("known-token", "known-password", "arbitrary_token", "sealed", "user:password"):
        assert secret not in serialized
    assert redacted["usage"]["completion_tokens"] == 5


async def _lines(items):
    for item in items:
        yield item


def test_sse_parser_preserves_real_framing():
    async def check():
        return [event async for event in runner.sse_events(_lines([
            ": heartbeat", "", "data: {\"type\": \"report_draft\",", "data: \"content\": \"test\"}", "",
            "data: [DONE]", "",
        ]))]
    assert asyncio.run(check()) == [{"type": "report_draft", "content": "test"}]


@pytest.mark.parametrize("lines", [["data: {\"type\":\"research_complete\"}"], ["data: []", ""]])
def test_sse_parser_does_not_accept_truncated_or_invalid_success(lines):
    async def check():
        return [event async for event in runner.sse_events(_lines(lines))]
    with pytest.raises(ValueError):
        asyncio.run(check())


def test_login_uses_new_normal_account_and_keeps_secrets_out_of_metadata():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/auth/register":
            assert json.loads(request.content)["username"].startswith("stage2_")
            return httpx.Response(201, json={"user": {"id": "new-account", "is_superuser": False},
                                            "access_token": "register-secret"})
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"user": {"id": "new-account"}, "access_token": "login-secret"})
        assert request.url.path == "/auth/me"
        assert request.headers["Authorization"] == "Bearer login-secret"
        return httpx.Response(200, json={"id": "new-account", "is_active": True})
    async def check():
        async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
            return await runner.register_test_account(client, "run-123456789012")
    account, secrets = asyncio.run(check())
    assert calls == ["/auth/register", "/auth/login", "/auth/me"]
    assert account["is_superuser"] is False
    assert "login-secret" in secrets and "login-secret" not in json.dumps(account)


def test_http_errors_never_echo_password_body():
    def handler(_request):
        return httpx.Response(422, json={"detail": [{"input": {"password": "never-archive-this"}}]})
    async def check():
        async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
            await runner.request_json(client, "POST", "/auth/register", json={})
    with pytest.raises(RuntimeError, match="HTTP 422") as raised:
        asyncio.run(check())
    assert "never-archive-this" not in str(raised.value)


@pytest.mark.parametrize("terminal", ["human_review_required", "research_complete", "missing"])
def test_http_stream_and_checkpoint_contract(tmp_path, terminal):
    uploads = []
    calls = []
    outcome = {"version": 1, "report_status": "restricted", "investigation_status": "stalled"}
    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        if path == "/knowledge-bases":
            return httpx.Response(201, json={"id": "new-kb", "name": "new-test-only"})
        if path == "/knowledge-bases/new-kb/documents" and request.method == "POST":
            name = runner.SCENARIOS["t01"]["files"][len(uploads)]
            doc = {"id": f"new-doc-{len(uploads)}", "filename": name, "status": "completed", "chunk_count": 1}
            uploads.append(doc)
            return httpx.Response(200, json=doc)
        if path == "/knowledge-bases/new-kb/documents":
            return httpx.Response(200, json=uploads)
        if path.endswith("/chunks"):
            return httpx.Response(200, json={"chunks": [{"index": 0, "content": "synthetic fixture"}]})
        if path == "/research/stream":
            body = json.loads(request.content)
            assert body["kb_name"] == "new-test-only"
            assert body["search_modes"] == ["local"]
            assert body["due_diligence"] is True and body["research_strategy"] == "agent"
            assert "company_profile_id" not in body
            events = [{"type": "report_draft", "content": {"content": "Report private-token"}}]
            if terminal != "missing":
                events.append({"type": terminal, "research_outcome": outcome})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join("data: " + json.dumps(event) + "\n\n" for event in events))
        if path.endswith("/full"):
            return httpx.Response(200, json={"success": True, "checkpoint": {
                "status": "paused" if terminal == "human_review_required" else "completed",
                "final_report": "Report private-token", "ui_state_json": {"research_outcome": outcome},
                "state_json": {"kb_scope": [{"kb_id": "new-kb"}], "password": "not-an-archive-field",
                               "agent_investigation": {"status": "stalled", "sources": {"private": "raw-source"}}},
            }})
        if "/cancel/" in path:
            return httpx.Response(200, json={"success": True})
        raise AssertionError(path)
    async def check():
        async with httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)) as client:
            return await runner.run_scenario(client, "t01", "run-123", tmp_path, ("private-token",),
                                             case_timeout=30, index_timeout=10, max_iterations=2)
    result = asyncio.run(check())
    assert result["quality_verdict"] == "not_scored"
    assert result["pipeline_pass"] is (terminal != "missing")
    assert not any("/review/" in path for _, path in calls)
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "t01").iterdir())
    assert "private-token" not in serialized and "not-an-archive-field" not in serialized and "raw-source" not in serialized
    if terminal == "missing":
        assert result["cancellation_requested"] is True
    else:
        assert not any("/cancel/" in path for _, path in calls)
