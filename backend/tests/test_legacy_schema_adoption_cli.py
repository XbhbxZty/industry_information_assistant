"""CLI input failures must happen before a database connection is constructed."""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from scripts import adopt_legacy_schema as cli  # noqa: E402
from core.legacy_schema_catalog import (  # noqa: E402
    LegacySchemaCatalogError, capture_legacy_schema_catalog_in_transaction,
)
from test_legacy_schema_catalog import _connection  # noqa: E402


def test_missing_host_policy_fails_before_engine(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_dotenv", lambda *args: None)
    monkeypatch.delenv("LEGACY_ADOPTION_POLICY_FILE", raising=False)
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: pytest.fail("must not connect"))
    assert cli.main(["apply", "--approval", str(tmp_path / "missing.json")]) == 3
    assert "adoption_configuration_or_connection_error" in capsys.readouterr().err


def test_write_capture_requires_explicit_read_write_mode():
    connection = _connection(read_only="off")
    with pytest.raises(LegacySchemaCatalogError, match="read-only"):
        capture_legacy_schema_catalog_in_transaction(connection)
    assert capture_legacy_schema_catalog_in_transaction(connection, read_only=False).snapshot_sha256
    with pytest.raises(LegacySchemaCatalogError, match="read-write"):
        capture_legacy_schema_catalog_in_transaction(_connection(), read_only=False)
