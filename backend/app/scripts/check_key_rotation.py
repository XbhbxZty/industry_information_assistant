"""Inspect key references and proposed retirements without changing keys/data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retire-audit-key", action="append", default=[], metavar="KEY_ID")
    parser.add_argument("--retire-checkpoint-key", action="append", default=[], metavar="KEY_ID")
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from core.database_url import resolve_database_urls

    engine = None
    try:
        load_dotenv(APP_DIR.parent / ".env")
        # ORM imports also resolve application configuration: load the selected
        # environment first and keep import/config errors inside the safe boundary.
        from service.key_rotation_preflight import inspect_key_rotation

        engine = create_engine(resolve_database_urls().sqlalchemy_url, poolclass=NullPool)
        report = inspect_key_rotation(
            engine, retire_audit_keys=args.retire_audit_key,
            retire_checkpoint_keys=args.retire_checkpoint_key,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["preflight_passed"] else 2
    except Exception as exc:
        # Neither configuration values nor DBAPI errors belong in CLI output.
        print(json.dumps({"preflight_passed": False, "error": "key_rotation_preflight_failed",
                          "error_type": type(exc).__name__}), file=sys.stderr)
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
