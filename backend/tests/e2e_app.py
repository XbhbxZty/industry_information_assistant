"""Process-only application entry point for the isolated browser E2E test.

This module is deliberately outside ``app/``: it is not a production feature
flag.  The supervisor must provide a fresh, disposable PostgreSQL URL and the
test-only signing material before this module is imported.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit


_DATABASE_NAME = re.compile(r"^codex_d2a2_preflight_[0-9a-f]{32}$")


def _require_isolated_environment() -> None:
    """Fail before importing the app when this is not the disposable target."""
    if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
        raise RuntimeError("e2e_app requires PYTHON_DOTENV_DISABLED=1")

    raw_url = os.getenv("DATABASE_URL", "")
    database = urlsplit(raw_url).path.lstrip("/")
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


_require_isolated_environment()

# Importing the real application preserves its router registration and, most
# importantly, its normal read-only Alembic-head lifespan guard.
import app_main  # noqa: E402
from service.deep_research_v2.service import DeepResearchV2Service  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402
from router import research_router  # noqa: E402
import service.scheduler_service as scheduler_service  # noqa: E402
import service.deep_research_v2.agents.wizard as wizard_module  # noqa: E402


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


def _e2e_research_service_v2() -> DeepResearchV2Service:
    """Build the real graph/PG saver, then replace only non-deterministic agents."""
    service = DeepResearchV2Service(
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
    return service


async def _skip_scheduler() -> None:
    """Test wrapper only; the real application lifespan still runs its guard."""


# Patch process-local import references before Uvicorn starts the app lifespan.
scheduler_service.init_scheduler_and_check_data = _skip_scheduler
research_router.get_research_service_v2 = _e2e_research_service_v2
research_router.get_research_service = lambda: {"research_service": _NoNetworkLegacyResearch()}
wizard_module.CodeWizard = _NoNetworkWizard
app = app_main.app
