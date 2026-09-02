"""Static contracts for the Alembic application-schema authority."""
from __future__ import annotations

import ast
import io
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
for path in (str(BACKEND), str(APP)):
    if path not in sys.path:
        sys.path.insert(0, path)

import models  # noqa: E402,F401
from core.database import Base  # noqa: E402


EXPECTED_APPLICATION_TABLES = {
    "admin_company_profile_audit_anchors",
    "admin_company_profile_audits",
    "admin_company_profiles",
    "bidding_info",
    "chat_attachments",
    "chat_messages",
    "chat_sessions",
    "company_data",
    "documents",
    "industry_news",
    "industry_stats",
    "knowledge_bases",
    "long_term_memories",
    "news_collection_tasks",
    "policy_data",
    "research_checkpoint_integrities",
    "research_checkpoints",
    "research_review_claims",
    "users",
}


def _config() -> Config:
    return Config(str(BACKEND / "alembic.ini"))


def test_migration_history_has_one_reviewed_baseline_and_one_head():
    script = ScriptDirectory.from_config(_config())
    assert script.get_bases() == ["20260831_0001"]
    assert script.get_heads() == ["20260902_0004"]
    revision = script.get_revision("20260831_0001")
    assert revision is not None
    assert revision.down_revision is None


def test_metadata_registers_the_current_application_schema():
    assert set(Base.metadata.tables) == EXPECTED_APPLICATION_TABLES


def test_data_dependent_upgrades_explicitly_reject_offline_sql():
    config = _config()
    config.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="在线升级"):
        command.upgrade(config, "20260831_0001:20260902_0002", sql=True)
    with pytest.raises(RuntimeError, match="在线升级"):
        command.upgrade(config, "20260902_0002:20260902_0003", sql=True)
    with pytest.raises(RuntimeError, match="在线升级"):
        command.upgrade(config, "20260902_0003:20260902_0004", sql=True)


def test_migration_keeps_existing_application_loggers_enabled():
    logger = logging.getLogger("checkpoint_context_security_test")
    logger.disabled = False
    config = _config()
    config.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="在线升级"):
        command.upgrade(config, "20260902_0002:20260902_0003", sql=True)
    assert logger.disabled is False


def test_migration_runtime_never_imports_fastapi_startup_or_calls_create_all():
    migration_files = [
        BACKEND / "migrations" / "env.py",
        *sorted((BACKEND / "migrations" / "versions").glob("*.py")),
    ]
    for migration_file in migration_files:
        tree = ast.parse(migration_file.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "app_main" not in imported_modules
        assert "app.app_main" not in imported_modules
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_all"
            for node in ast.walk(tree)
        )


def test_importing_url_resolver_does_not_construct_the_application_engine():
    code = (
        f"import sys; sys.path.insert(0, {str(APP)!r}); import core.database_url; "
        "assert 'core.database' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
