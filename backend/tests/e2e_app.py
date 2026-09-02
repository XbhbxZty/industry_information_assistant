"""Process-only application entry point for isolated HTTP-process acceptance.

This module is deliberately outside ``app/``: it is not a production feature
flag.  The supervisor must provide a fresh, disposable PostgreSQL URL and the
test-only signing material before this module is imported.
"""
from __future__ import annotations

import os
import re
import socket
import importlib
from typing import Any
from urllib.parse import unquote, urlsplit


_DATABASE_NAME = re.compile(r"^codex_d2a2_preflight_[0-9a-f]{32}$")
_POSTGRES_SCHEMES = frozenset((
    "postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2",
))
_LOCAL_DATABASE_HOSTS = frozenset(("127.0.0.1", "::1", "localhost"))
_PAUSE_EVENTS = frozenset(("research_resumed", "human_review_completed"))


def _require_isolated_environment() -> None:
    """Fail before importing the app when this is not the disposable target."""
    if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
        raise RuntimeError("e2e_app requires PYTHON_DOTENV_DISABLED=1")

    raw_url = os.getenv("DATABASE_URL", "")
    parsed = urlsplit(raw_url)
    if parsed.scheme not in _POSTGRES_SCHEMES:
        raise RuntimeError("e2e_app requires a PostgreSQL DATABASE_URL")
    # libpq accepts connection parameters in the query string (for example
    # ``?host=remote`` or ``?service=production``).  The parsed authority
    # alone therefore cannot prove the child will stay on the local target.
    if parsed.query:
        raise RuntimeError("e2e_app refuses DATABASE_URL query overrides")
    if (parsed.hostname or "").lower() not in _LOCAL_DATABASE_HOSTS:
        raise RuntimeError("e2e_app refuses a non-local PostgreSQL host")
    database = unquote(parsed.path.lstrip("/"))
    if not _DATABASE_NAME.fullmatch(database):
        raise RuntimeError("e2e_app refuses a DATABASE_URL outside its disposable database prefix")

    # These are security inputs for persisted checkpoints/profile audit history.
    # Require caller supplied values so this process never falls back to the
    # development defaults in application modules.
    for variable in (
        "JWT_SECRET_KEY",
        "ADMIN_PROFILE_SNAPSHOT_HMAC_KEY",
        "COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID",
        "COMPANY_PROFILE_AUDIT_KEYS_JSON",
    ):
        if not os.getenv(variable):
            raise RuntimeError(f"e2e_app requires {variable}")


def _guard_outbound_connections() -> None:
    """Keep an accidental provider call inside this disposable process local.

    The app itself is allowed to connect to the local PostgreSQL instance; the
    acceptance supervisor is the separate process which connects to Uvicorn.
    This is deliberately process-local test scaffolding, never application
    policy.
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _is_local(address: object) -> bool:
        if not isinstance(address, tuple) or not address:
            return True  # Unix sockets do not leave this machine.
        host = address[0]
        return isinstance(host, str) and host.lower() in _LOCAL_DATABASE_HOSTS

    def _blocked_connect(sock: socket.socket, address: object) -> Any:
        if not _is_local(address):
            raise OSError("e2e_app blocked non-local outbound connection")
        return original_connect(sock, address)

    def _blocked_connect_ex(sock: socket.socket, address: object) -> int:
        if not _is_local(address):
            return getattr(socket, "EACCES", 13)
        return original_connect_ex(sock, address)

    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex


def _validate_signing_configuration() -> None:
    """Fail before serving if the supplied persistence signing configuration is bad."""
    from core.checkpoint_keys import load_checkpoint_keyring
    from core.company_profile_audit_keys import load_company_profile_audit_keyring

    try:
        load_checkpoint_keyring()
        load_company_profile_audit_keyring()
    except Exception as exc:
        raise RuntimeError("e2e_app refuses invalid persistence signing configuration") from exc


_require_isolated_environment()
_guard_outbound_connections()

# app_main normally calls load_dotenv() during import.  The acceptance process
# gets every value from its child environment; prevent an adjacent developer
# .env from becoming an implicit, unreviewed input.
import dotenv  # noqa: E402


def _disabled_load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
    return False


dotenv.load_dotenv = _disabled_load_dotenv
_validate_signing_configuration()

# Importing the real application preserves its router registration and, most
# importantly, its normal read-only Alembic-head lifespan guard.
import app_main  # noqa: E402
from service.deep_research_v2.service import DeepResearchV2Service  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402
# ``router.__init__`` exports its ``APIRouter`` under the same name.  Import
# the module explicitly: the dependency factories live on that module, not on
# the exported router object.
research_router = importlib.import_module("router.research_router")  # noqa: E402
import service.scheduler_service as scheduler_service  # noqa: E402
import service.deep_research_v2.agents.wizard as wizard_module  # noqa: E402
import service.deep_research_v2.graph as graph_module  # noqa: E402


class _DeterministicAgent:
    """No-network stand-in for model/search agents in this test process only."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.as_of = ""

    async def process(self, state: dict[str, Any]) -> dict[str, Any]:
        if self.kind == "architect":
            state["outline"] = [
                {
                    "id": f"sec_{index}", "title": title,
                    "description": "E2E deterministic outline",
                    "section_type": "quantitative" if index in (3, 4) else "qualitative",
                    "requires_data": index in (3, 4), "requires_chart": False,
                    "priority": index, "search_queries": [title], "status": "pending",
                }
                for index, title in enumerate((
                    "企业基本情况", "股权结构与实际控制人", "经营状况", "财务分析",
                    "司法与合规风险", "关联关系与对外担保", "舆情扫描", "风险汇总与授信建议",
                ), start=1)
            ]
            state["research_questions"] = ["测试档案是否可审计"]
            state["hypotheses"] = []
            state["knowledge_graph"] = {"nodes": [], "edges": []}
        elif self.kind == "scout":
            for section in state.get("outline", []):
                section["status"] = "completed"
            # Search intentionally returns no invented evidence.  The profile
            # hand-off and deterministic scoring remain the system under test.
            state.setdefault("search_failures", []).append({
                "source": "e2e_deterministic_search", "reason": "external search disabled",
            })
        elif self.kind == "writer":
            risk = state.get("risk_assessment") or {}
            state["final_report"] = (
                "# E2E 尽调报告\n\n"
                "本报告由隔离测试入口生成；未调用外部模型或搜索服务。\n\n"
                f"当前风险等级：{risk.get('level', '未评级')}。"
            )
        elif self.kind == "critic":
            state["iteration"] = int(state.get("iteration", 0)) + 1
            state["phase"] = ResearchPhase.COMPLETED.value
            state["unresolved_issues"] = []
        return state


class _NoNetworkWizard(_DeterministicAgent):
    """Constructor-compatible replacement for the admin-only wizard probe."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        super().__init__("E2EWizard", "wizard")


class _NoNetworkScout(_DeterministicAgent):
    """Constructor-compatible replacement before DeepResearchGraph is built.

    ``DeepScout.__init__`` opens its Milvus client.  Replacing an already
    constructed ``graph.scout`` is therefore too late for process isolation.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        super().__init__("E2EScout", "scout")


class _NoNetworkLegacyResearch:
    """Ensure a deliberately requested v1 stream cannot reach its providers."""

    async def research_stream(self, *_args: Any, **_kwargs: Any):
        yield '{"type":"error","content":"v1 research is disabled in the E2E server"}'


async def _deterministic_llm(*_args: Any, return_meta: bool = False, **_kwargs: Any):
    """Keep the real DataAnalyst instance, but make every residual LLM path inert."""
    response = "{}"
    if return_meta:
        return response, {
            "finish_reason": "stop", "completion_tokens": 0,
            "prompt_tokens": 0, "duration_ms": 0,
        }
    return response


def _require_postgres_graph_saver() -> None:
    """Reject the graph module's documented MemorySaver fallback in E2E."""
    from langgraph.checkpoint.postgres import PostgresSaver

    if not isinstance(getattr(graph_module, "_CHECKPOINTER", None), PostgresSaver):
        raise RuntimeError("e2e_app requires the real PostgreSQL LangGraph saver")


class _PausedReviewService(DeepResearchV2Service):
    """SSE-only test wrapper used by the supervisor's hard-kill recovery run."""

    async def submit_review(self, *args: Any, **kwargs: Any):
        pause_at = os.getenv("E2E_REVIEW_PAUSE_AT", "")
        if pause_at and pause_at not in _PAUSE_EVENTS:
            raise RuntimeError("E2E_REVIEW_PAUSE_AT is not a supported review event")
        paused = False
        async for chunk in super().submit_review(*args, **kwargs):
            yield chunk
            if not paused and pause_at and f'"type": "{pause_at}"' in chunk:
                paused = True
                # The event has crossed the HTTP boundary; the supervisor may
                # now close the stream and terminate this process.  No DB or
                # graph state is altered by this wrapper.
                import asyncio
                await asyncio.sleep(30)


def _e2e_research_service_v2() -> DeepResearchV2Service:
    """Build the real graph/PG saver, then replace only non-deterministic agents."""
    service = _PausedReviewService(
        llm_api_key="e2e-no-network",
        llm_base_url="http://127.0.0.1:9",
        search_api_key="e2e-no-network",
        model="e2e-deterministic",
        max_iterations=1,
    )
    graph = service.graph
    graph.architect = _DeterministicAgent("E2EArchitect", "architect")
    graph.scout = _DeterministicAgent("E2EScout", "scout")
    graph.wizard = _DeterministicAgent("E2EWizard", "wizard")
    graph.writer = _DeterministicAgent("E2EWriter", "writer")
    graph.critic = _DeterministicAgent("E2ECritic", "critic")

    # DataAnalyst (including its rule scorer) remains the production class.
    # Its LLM helper is replaced too, so an accidental non-DD request cannot
    # escape to a configured provider from the test server.
    graph.data_analyst.call_llm = _deterministic_llm
    _require_postgres_graph_saver()
    return service


async def _skip_scheduler() -> None:
    """Test wrapper only; the real application lifespan still runs its guard."""


# Patch process-local import references before Uvicorn starts the app lifespan.
scheduler_service.init_scheduler_and_check_data = _skip_scheduler
# ``DeepResearchGraph.__init__`` resolves this global while constructing its
# agents.  Patch it before any ``DeepResearchV2Service`` is created so Scout
# cannot initialize a real Milvus client during acceptance startup.
graph_module.DeepScout = _NoNetworkScout
# ``Depends(get_research_service)`` captured the original function when the
# route was declared.  Override that exact callable; reassigning the module
# attribute would leave v1 able to instantiate its configured service.
_original_get_research_service = research_router.get_research_service
app_main.app.dependency_overrides[_original_get_research_service] = (
    lambda: {"research_service": _NoNetworkLegacyResearch()}
)
# V2 factories are regular global lookups performed inside endpoint bodies.
research_router.get_research_service_v2 = _e2e_research_service_v2
wizard_module.CodeWizard = _NoNetworkWizard
app = app_main.app
