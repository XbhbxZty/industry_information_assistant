"""Prevent the host-side Milvus port from drifting across launch surfaces."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_milvus_default_port_is_consistent():
    expected = "40030"
    files = {
        "environment template": ROOT / "backend" / ".env.example",
        "root compose": ROOT / "docker-compose.yml",
        "backend compose": ROOT / "backend" / "docker-compose-base.yml",
        "PowerShell launcher": ROOT / "start-app.ps1",
        "shell launcher": ROOT / "start-services.sh",
        "service config": ROOT / "backend" / "app" / "service" / "config.py",
        "Milvus service": ROOT / "backend" / "app" / "service" / "milvus_service.py",
        "policy search": ROOT / "backend" / "app" / "service" / "policy_search_service.py",
    }
    for label, path in files.items():
        content = path.read_text(encoding="utf-8")
        assert expected in content, f"{label} no longer uses the shared Milvus default"
        assert "29530" not in content, f"{label} still contains the retired Milvus host port"
