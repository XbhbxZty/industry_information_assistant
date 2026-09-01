"""Read-only operator preflight for an unversioned PostgreSQL database.

The target is resolved from the same DATABASE_URL/POSTGRES_* authority as the
application.  This command deliberately has no stamp or mutation option.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import create_engine, pool

from core.database_url import resolve_database_urls
from core.legacy_schema_preflight import (
    LegacyPreflightError,
    PreflightStatus,
    preflight_legacy_schema,
)


def _stable_error_code(exc: Exception) -> str:
    if isinstance(exc, LegacyPreflightError):
        return "legacy_preflight_invalid"
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "42501":
        return "legacy_preflight_permission_denied"
    return "legacy_preflight_failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent the JSON report for operator review",
    )
    args = parser.parse_args(argv)

    engine = None
    try:
        database_url = resolve_database_urls().sqlalchemy_url
        engine = create_engine(database_url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            report = preflight_legacy_schema(connection)
        print(json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        ))
        if report.status in {
            PreflightStatus.UPGRADE_REQUIRED,
            PreflightStatus.EXACT_ADOPTABLE,
            PreflightStatus.ALREADY_MANAGED,
        }:
            return 0
        return 2
    except Exception as exc:
        # Do not echo DBAPI exception text: some drivers include connection
        # details.  The stable type is enough for a fail-closed CLI result.
        print(json.dumps({
            "status": "preflight_error",
            "error_code": _stable_error_code(exc),
            "error_type": type(exc).__name__,
        }, sort_keys=True), file=sys.stderr)
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
