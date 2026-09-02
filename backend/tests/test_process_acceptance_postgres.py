"""Development acceptance without a browser: real TCP, JWT, PG and restart.

Run with MIGRATION_TEST_ADMIN_URL and pytest. Only synthetic users/materials
are created, in the existing identity-checked disposable database fixture.
The child runs the actual application and PostgreSQL LangGraph saver; external
models/search are deterministic test substitutes (not report-quality testing).
"""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

from alembic import command
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from test_legacy_schema_preflight_postgres import (  # noqa: F401
    disposable_postgres_database, _alembic_config,
)
from models.user import User
from models.knowledge import KnowledgeBase, Document
from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity, ResearchReviewClaim
from core.security import get_password_hash


pytestmark = pytest.mark.postgres_integration
BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "Synthetic-process-test-only-2026!"
APPROVE = {"approved": True, "comment": "独立进程测试确认", "override_level": None}
PRIVATE_MARKER = "PRIVATE_KNOWLEDGE_MUST_NOT_APPEAR_IN_PROFILE_REVIEW"


def _validate_local_admin_url(raw_url):
    url = make_url(raw_url)
    if (url.get_backend_name() not in {"postgres", "postgresql"}
            or (url.host or "").lower() not in {"localhost", "127.0.0.1", "::1"}
            or url.query):
        raise ValueError("process acceptance requires a local PostgreSQL URL without query overrides")


@pytest.fixture(autouse=True)
def _local_postgres_target():
    # Autouse runs before other function fixtures: reject a remote/misdirected
    # admin target BEFORE the shared disposable fixture can create a database.
    raw_url = os.getenv("MIGRATION_TEST_ADMIN_URL")
    if raw_url:
        _validate_local_admin_url(raw_url)


class ProcessServer:
    def __init__(self, target, engine, identities, folder):
        self.target, self.engine, self.identities = target, engine, identities
        self.folder = folder
        self.process = None
        self.log = None
        self.generation = 0
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # Do not inherit provider credentials, proxy settings or the user's .env.
        system_keys = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
                       "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES"}
        self.env = {key: value for key, value in os.environ.items() if key.upper() in system_keys}
        self.env.update({
            "PYTHONPATH": os.pathsep.join((str(BACKEND / "tests"), str(BACKEND / "app"))),
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHON_DOTENV_DISABLED": "1",
            "DATABASE_URL": target.sqlalchemy_url,
            "JWT_SECRET_KEY": secrets.token_hex(32),
            "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY": secrets.token_hex(32),
            "COMPANY_PROFILE_AUDIT_KEYS_JSON": json.dumps({
                "process-test": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
            }),
            "COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID": "process-test",
            "DD_HUMAN_REVIEWER_USER_IDS": ",".join(str(identities[key]) for key in ("reviewer", "peer")),
            "DASHSCOPE_API_KEY": "e2e-no-network", "BOCHA_API_KEY": "e2e-no-network",
            "DASHSCOPE_BASE_URL": "http://127.0.0.1:9", "OPENAI_MODEL": "e2e-deterministic",
            "OPENROUTER_API_KEY": "e2e-no-network", "OPENAI_API_KEY": "e2e-no-network",
        })

    def start(self, pause_at=""):
        assert self.process is None
        self.generation += 1
        log_path = self.folder / f"server-{self.generation}.log"
        self.log = log_path.open("wb")
        env = {**self.env, "E2E_REVIEW_PAUSE_AT": pause_at}
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "e2e_app:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--no-access-log"],
            cwd=BACKEND, env=env, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 45
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    pytest.fail(f"isolated server exited {self.process.returncode}; inspect {log_path}")
                try:
                    response = client.get(self.url + "/hello")
                    if response.status_code == 200 and response.json().get("status") == "success":
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.1)
        pytest.fail(f"isolated server did not become ready; inspect {log_path}")

    def stop(self):
        if self.process is not None:
            # Only the child Popen object created above; never enumerate/kill other services.
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def restart(self, pause_at=""):
        old_pid = self.process.pid
        self.stop()
        self.start(pause_at)
        assert self.process.pid != old_pid

    @contextmanager
    def login(self, role):
        with httpx.Client(base_url=self.url, timeout=30, trust_env=False) as client:
            response = client.post("/auth/login", json={"username": f"e2e_{role}", "password": PASSWORD})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["user"]["id"] == str(self.identities[role])
            assert data["user"]["can_human_review"] is (role in {"admin", "reviewer", "peer"})
            client.headers["Authorization"] = "Bearer " + data["access_token"]
            yield client

    def checkpoint(self, session_id):
        with Session(self.engine) as db:
            cp = db.query(ResearchCheckpoint).filter_by(session_id=session_id).one()
            integrity = db.query(ResearchCheckpointIntegrity).filter_by(session_id=session_id).one()
            claim = db.query(ResearchReviewClaim).filter_by(checkpoint_id=cp.id).one_or_none()
            return {
                "status": cp.status, "owner": str(cp.user_id), "state": copy.deepcopy(cp.state_json),
                "revision": integrity.business_revision, "report": cp.final_report,
                "claim": None if claim is None else {
                    "state": claim.state, "reviewer_id": str(claim.reviewer_id),
                    "decision": copy.deepcopy(claim.decision_json),
                    "accepted_at": claim.accepted_at.isoformat(),
                },
            }


@pytest.fixture
def process_server(disposable_postgres_database, tmp_path):
    target = disposable_postgres_database
    # The actual migration reads the caller's explicit target; do not load user .env.
    with patch("dotenv.load_dotenv", return_value=False):
        command.upgrade(_alembic_config(target), "head")
    engine = create_engine(target.sqlalchemy_url)
    identities = {role: uuid.uuid4() for role in ("admin", "owner", "reviewer", "peer")}
    server = ProcessServer(target, engine, identities, tmp_path)
    try:
        with Session(engine) as db:
            hashed = get_password_hash(PASSWORD)
            for role, user_id in identities.items():
                db.add(User(id=user_id, username=f"e2e_{role}", email=f"e2e_{role}@example.com",
                            hashed_password=hashed, is_active=True, is_superuser=role == "admin"))
            db.flush()
            for role in ("owner", "reviewer"):
                kb = KnowledgeBase(user_id=identities[role], name=f"{role}_private", description=PRIVATE_MARKER)
                db.add(kb)
                db.flush()
                db.add(Document(knowledge_base_id=kb.id, user_id=identities[role],
                                filename=PRIVATE_MARKER + ".txt", file_type="txt", status="completed"))
            db.commit()
        server.start()
        yield server
    finally:
        server.stop()
        engine.dispose()
    # The parent fixture verifies database OID/owner/cluster before dropping it.


def _body(name="独立进程验收企业有限公司"):
    return {
        "profile": {"name": name, "registration": {
            "registered_capital": "100万元", "paid_in_capital": "100万元",
            "established_date": "2020-01-01", "legal_representative": "测试甲",
            "company_type": "有限责任公司", "operating_status": "存续", "business_scope": "技术服务",
        }},
        "scenario": "factoring", "scenario_data": {"accounts_receivable_gross": 1000},
        "field_sources": [{
            "source_id": "synthetic-register", "name": "合成登记证据", "issuer": "测试登记机关",
            "source_type": "official", "field_ids": ["registration", "operating_status", "business_scope"],
            "retrieved_at": "2026-08-20T00:00:00+00:00", "as_of_date": "2026-08-20",
            "reference": "test-fixture://registration",
        }, {
            "source_id": "synthetic-receivable", "name": "合成应收账款授权证明", "issuer": "合成交易对手",
            "source_type": "authorized", "field_ids": ["accounts_receivable_gross"],
            "retrieved_at": "2026-08-20T00:00:00+00:00", "as_of_date": "2026-08-20",
            "reference": "test-fixture://receivable",
        }],
        "materials": [{"title": "企业补充说明", "source_type": "company_submitted",
                       "content": "合成企业自报回款计划；未经独立核实。", "as_of_date": "2026-08-20"}],
        "change_reason": "合成验收数据，不代表真实企业",
    }


def _json(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _events(response):
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ") and line[6:] != "[DONE]"]
    assert not any(item.get("type") == "error" for item in events), events
    return events


def _research(client, profile_id, session_id=None):
    session_id = session_id or str(uuid.uuid4())
    response = client.post("/research/stream", json={
        "query": "请对独立进程验收企业有限公司进行贷前尽职调查", "version": "v2",
        "session_id": session_id, "due_diligence": True, "search_modes": [],
        "company_profile_id": profile_id, "as_of": "2026-08-27",
    })
    events = _events(response)
    assert events[-1]["type"] == "human_review_required", events
    return session_id, events


def test_real_login_profile_workflow_freezing_review_and_archive(process_server):
    server = process_server
    with server.login("admin") as admin, server.login("owner") as owner, server.login("reviewer") as reviewer:
        assert httpx.get(server.url + "/company-profiles", trust_env=False).status_code == 401
        assert owner.post("/company-profiles", json=_body()).status_code == 403
        assert reviewer.post("/company-profiles", json=_body()).status_code == 403
        sparse = _json(admin.post("/company-profiles", json={"profile": {"name": "只有名称也可以保存"}}), 201)
        assert sparse["revision"] == 1
        profile_id = sparse["id"]
        payload = {**_body(), "expected_revision": 1}
        profile = _json(admin.put(f"/company-profiles/{profile_id}", json=payload))
        assert profile["revision"] == 2 and profile["status"] == "active"
        assert admin.put(f"/company-profiles/{profile_id}", json=payload).status_code == 409
        materials = _json(owner.post(f"/company-profiles/{profile_id}/materials/search", json={"query": "回款"}))
        assert materials["total"] == 1 and materials["items"][0]["eligible_for_structured_evidence"] is False
        before_kb = _json(owner.get("/knowledge-bases"))
        assert [kb["name"] for kb in before_kb] == ["owner_private"]
        session_id, _ = _research(owner, profile_id)
        frozen = server.checkpoint(session_id)
        assert frozen["status"] == "paused" and frozen["owner"] == str(server.identities["owner"])
        assert frozen["state"]["admin_profile_ref"]["revision"] == 2
        checks = frozen["state"]["field_checks"]
        assert next(item for item in checks if item["field_id"] == "registration")["status"] == "verified"
        assert any(item["status"] == "unverified" for item in checks)
        assert "合成企业自报回款计划" not in json.dumps(frozen["state"].get("evidence_store"), ensure_ascii=False)
        # Prove the run is backed by the PostgreSQL graph provider, not MemorySaver.
        with server.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id=:id"), {"id": session_id}).scalar() > 0
        updated = _json(admin.put(f"/company-profiles/{profile_id}", json={
            **_body("独立进程验收企业后续版本有限公司"), "expected_revision": 2,
        }))
        assert updated["revision"] == 3
        server.restart()
        # Same JWT survives restart; authorization still comes from actual DB users.
        assert _json(owner.get("/auth/me"))["id"] == str(server.identities["owner"])
        assert any(item["session_id"] == session_id for item in _json(reviewer.get("/research/reviews"))["items"])
        packet_response = reviewer.get(f"/research/reviews/{session_id}")
        packet = _json(packet_response)
        assert packet["profile_ref"]["revision"] == 2
        assert PRIVATE_MARKER not in packet_response.text and "state_json" not in packet_response.text
        assert reviewer.get(f"/research/checkpoint/{session_id}/full").status_code == 403
        assert owner.post(f"/research/review/{session_id}", json=APPROVE).status_code == 403
        assert reviewer.post(f"/research/review/{session_id}", json={"approved": False}).status_code == 422
        assert server.checkpoint(session_id)["claim"] is None
        decision = {"approved": True, "comment": "合成测试改判，保留规则原判", "override_level": "高风险"}
        completed = _events(reviewer.post(f"/research/review/{session_id}", json=decision))[-1]
        assert completed["type"] == "research_complete"
        saved = server.checkpoint(session_id)
        assert saved["status"] == "completed" and saved["claim"]["state"] == "finalized"
        assert saved["revision"] == frozen["revision"] + 1
        assert saved["state"]["admin_profile_ref"] == frozen["state"]["admin_profile_ref"]
        human = saved["state"]["risk_assessment"]["human_review"]
        assert human["reviewer"] == "e2e_reviewer" and human["reviewed_at"] == saved["claim"]["accepted_at"]
        assert saved["state"]["risk_assessment"]["level"] == "高风险"
        assert "合成测试改判" in saved["report"]
        assert _events(reviewer.post(f"/research/review/{session_id}", json=decision))[-1] == completed
        assert server.checkpoint(session_id) == saved
        full = _json(owner.get(f"/research/checkpoint/{session_id}/full"))["checkpoint"]
        assert full["final_report"] == saved["report"]
        archive = _json(admin.post(f"/company-profiles/{profile_id}/archive", json={
            "expected_revision": 3, "change_reason": "验收归档",
        }))
        assert archive["status"] == "archived" and archive["revision"] == 4
        assert all(item["id"] != profile_id for item in _json(owner.get("/company-profiles"))["items"])
        history = _json(admin.get(f"/company-profiles/{profile_id}/history"))
        assert sorted(item["revision"] for item in history["items"]) == [1, 2, 3, 4]
        assert _json(owner.get("/knowledge-bases")) == before_kb
        with server.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM knowledge_bases")).scalar() == 2
            assert connection.execute(text("SELECT count(*) FROM documents")).scalar() == 2


@pytest.mark.parametrize("pause_at", ["research_resumed", "human_review_completed"])
def test_real_http_disconnect_and_process_kill_recover_original_decision(process_server, pause_at):
    server = process_server
    with server.login("admin") as admin, server.login("owner") as owner, server.login("reviewer") as reviewer, server.login("peer") as peer:
        profile = _json(admin.post("/company-profiles", json=_body()), 201)
        session_id, _ = _research(owner, profile["id"])
        before = server.checkpoint(session_id)
        server.restart(pause_at)
        received = []
        # The test-only transport wrapper pauses after a real event. Closing the
        # TCP stream and killing the owning child leave the actual DB outcome intact.
        with reviewer.stream("POST", f"/research/review/{session_id}", json=APPROVE) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    item = json.loads(line[6:])
                    received.append(item["type"])
                    assert item["type"] != "error", item
                    if item["type"] == pause_at:
                        break
        assert received[-1] == pause_at and "research_complete" not in received
        server.stop()
        interrupted = server.checkpoint(session_id)
        assert interrupted["claim"]["state"] == ("accepted" if pause_at == "research_resumed" else "finalized")
        assert interrupted["status"] == ("paused" if pause_at == "research_resumed" else "completed")
        server.start()
        assert peer.post(f"/research/review/{session_id}", json=APPROVE).status_code == 409
        assert reviewer.post(f"/research/review/{session_id}", json={"approved": False, "comment": "changed"}).status_code == 409
        result = _events(reviewer.post(f"/research/review/{session_id}", json=APPROVE))[-1]
        assert result["type"] == "research_complete"
        final = server.checkpoint(session_id)
        assert final["revision"] == before["revision"] + 1
        assert final["claim"]["decision"] == interrupted["claim"]["decision"]
        assert final["claim"]["accepted_at"] == interrupted["claim"]["accepted_at"]
        assert final["state"]["risk_assessment"]["human_review"]["reviewed_at"] == final["claim"]["accepted_at"]


def test_http_rejects_tampered_profile_and_pending_checkpoint(process_server):
    server = process_server
    with server.login("admin") as admin, server.login("owner") as owner, server.login("reviewer") as reviewer:
        profile = _json(admin.post("/company-profiles", json=_body()), 201)
        session_id, _ = _research(owner, profile["id"])
        # Fault injection only in this fixture's freshly created disposable DB.
        with server.engine.begin() as connection:
            connection.execute(text("UPDATE research_checkpoints SET state_json=jsonb_set(state_json, '{query}', '\"tampered\"') WHERE session_id=:id"), {"id": session_id})
        assert reviewer.get(f"/research/reviews/{session_id}").status_code == 409
        assert reviewer.post(f"/research/review/{session_id}", json=APPROVE).status_code == 409
        assert server.checkpoint(session_id)["claim"] is None
        with server.engine.begin() as connection:
            connection.execute(text("UPDATE admin_company_profiles SET profile_json=jsonb_set(CAST(profile_json AS jsonb), '{name}', '\"tampered\"') WHERE id=:id"), {"id": profile["id"]})
        assert admin.get(f"/company-profiles/{profile['id']}").status_code == 409
        response = owner.post("/research/stream", json={
            "query": "请对测试企业尽调", "version": "v2", "search_modes": [], "company_profile_id": profile["id"],
        })
        assert response.status_code == 409 and "text/event-stream" not in response.headers.get("content-type", "")
