"""Inspect or explicitly adopt a supported legacy database from a local terminal.

Run as python app/scripts/adopt_legacy_schema.py from backend/. Apply requires an approval
file and LEGACY_ADOPTION_POLICY_FILE from host configuration. Neither command
is an HTTP endpoint; the operator needs host access and database credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from core.database_url import resolve_database_urls
from core.legacy_schema_adoption import (
    ADOPTABLE_PROFILE_ID, APPROVAL_FORMAT, MAX_APPROVAL_TTL,
    TRUSTED_TARGET_POLICY_FORMAT, TrustedTargetPolicy,
    load_adoption_approval, load_trusted_target_policy, trusted_target_policy_sha256,
)
from core.legacy_schema_adoption_executor import (
    BACKEND, adopt_legacy_schema, observe_target_identity, postgres_now,
)
from core.legacy_schema_catalog import legacy_schema_read_transaction
from core.legacy_schema_preflight import preflight_legacy_schema_in_transaction


def inspect_adoption(connection) -> dict:
    """Generate reviewable templates only; blanks make them invalid for apply."""
    with legacy_schema_read_transaction(connection):
        report = preflight_legacy_schema_in_transaction(connection)
        identity = observe_target_identity(connection)
        now = postgres_now(connection)
    policy = TrustedTargetPolicy(str(uuid.uuid4()), **identity, environment_id="development")
    return {
        "preflight": report.to_dict(),
        "policy_template": {
            "format": TRUSTED_TARGET_POLICY_FORMAT, "policy_id": policy.policy_id,
            **identity, "environment_id": policy.environment_id,
        },
        "approval_template": {
            "format": APPROVAL_FORMAT, "approval_id": str(uuid.uuid4()),
            "operator_reference": "", "profile_id": ADOPTABLE_PROFILE_ID,
            "target_policy_id": policy.policy_id,
            "expected_target_policy_sha256": trusted_target_policy_sha256(policy),
            "expected_preflight_sha256": report.preflight_sha256,
            "operator_attested_backup_reference": "",
            "operator_attested_maintenance_window_reference": "",
            "confirmation_phrase": "",
            "confirmed_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "expires_at": (now + MAX_APPROVAL_TTL).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="read-only preflight and JSON templates")
    apply = commands.add_parser("apply", help="explicit transactional adoption")
    apply.add_argument("--approval", type=Path, required=True)
    args = parser.parse_args(argv)
    engine = None
    try:
        load_dotenv(BACKEND / ".env")
        if args.command == "apply":
            policy_path = os.environ.get("LEGACY_ADOPTION_POLICY_FILE")
            if not policy_path:
                raise ValueError("LEGACY_ADOPTION_POLICY_FILE is required")
            policy = load_trusted_target_policy(Path(policy_path).read_text(encoding="utf-8-sig"))
            approval = load_adoption_approval(args.approval.read_text(encoding="utf-8-sig"))
        engine = create_engine(resolve_database_urls().sqlalchemy_url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            if args.command == "inspect":
                payload = inspect_adoption(connection)
                code = 0
            else:
                result = adopt_legacy_schema(connection, approval, policy)
                payload = result.to_dict()
                code = {"adopted": 0, "already_managed": 0, "outcome_unknown": 4}.get(
                    result.status, 2,
                )
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
        return code
    except Exception as exc:
        # Never include driver text, URLs, raw file contents or credentials.
        code = getattr(getattr(exc, "code", None), "value", "adoption_configuration_or_connection_error")
        print(json.dumps({"status": "error", "error_code": code}), file=sys.stderr)
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
