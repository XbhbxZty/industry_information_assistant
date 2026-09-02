"""Local maintenance execution, deliberately not exposed through an HTTP API.

Access to the host configuration and PostgreSQL maintenance credentials is the
authority boundary. Approval fields are operator attestations, not signatures.
Stop external writers before using this tool; locks cannot exclude unrelated
new-object DDL. Only the frozen PostgreSQL 15 base schema is supported.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

from .legacy_schema_adoption import (
    AdoptionApproval,
    AdoptionValidationCode,
    LegacySchemaAdoptionError,
    TrustedTargetPolicy,
    compute_database_identity_sha256,
    compute_server_identity_sha256,
    serialize_adoption_approval,
    serialize_trusted_target_policy,
    validate_adoption_approval,
    validate_adoption_contract,
    verify_observed_target,
)
from .legacy_schema_preflight import (
    APPLICATION_TABLES,
    HEAD_REVISION,
    PreflightStatus,
    preflight_legacy_schema_in_transaction,
)


ADVISORY_NAMESPACE = 1229537604
ADVISORY_PROTOCOL = 1144144178
BACKEND = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AdoptionResult:
    status: str
    approval_id: str
    operator_reference: str
    error_code: str | None = None
    revision: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def postgres_now(connection: Connection) -> datetime:
    """Use wall-clock time, not PostgreSQL's transaction-start timestamp."""
    return connection.exec_driver_sql(
        "SELECT pg_catalog.clock_timestamp()"
    ).scalar_one().astimezone(timezone.utc)


def observe_target_identity(connection: Connection) -> dict[str, str]:
    row = connection.exec_driver_sql("""
        SELECT (pg_catalog.pg_control_system()).system_identifier::text AS system_identifier,
               pg_catalog.host(pg_catalog.inet_server_addr()) AS server_address,
               pg_catalog.inet_server_port() AS server_port,
               pg_catalog.current_setting('server_version_num') AS server_version_num,
               d.datname AS database_name, d.oid::bigint AS database_oid,
               pg_catalog.pg_get_userbyid(d.datdba) AS database_owner
        FROM pg_catalog.pg_database AS d
        WHERE d.datname = pg_catalog.current_database()
    """).mappings().one()
    return {
        "server_identity_sha256": compute_server_identity_sha256(
            system_identifier=row["system_identifier"],
            server_address=row["server_address"],
            server_port=row["server_port"],
            server_version_num=row["server_version_num"],
        ),
        "database_identity_sha256": compute_database_identity_sha256(
            database_name=row["database_name"], database_oid=row["database_oid"],
            database_owner=row["database_owner"],
        ),
    }


def _stamp(connection: Connection) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["connection"] = connection
    command.stamp(config, HEAD_REVISION, purge=False)


def _verify_stamp(connection: Connection) -> None:
    revisions = connection.exec_driver_sql(
        "SELECT version_num FROM public.alembic_version ORDER BY version_num"
    ).scalars().all()
    if revisions != [HEAD_REVISION]:
        raise LegacySchemaAdoptionError(
            AdoptionValidationCode.PREFLIGHT_MISMATCH, "stamp postverification failed",
        )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, LegacySchemaAdoptionError):
        return exc.code.value
    original = getattr(exc, "orig", exc)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return {
        "55P03": "adoption_busy",
        "42501": "permission_denied",
        "42P01": "preflight_mismatch",
    }.get(sqlstate, "adoption_failed")


def adopt_legacy_schema(
    connection: Connection, approval: AdoptionApproval, policy: TrustedTargetPolicy,
) -> AdoptionResult:
    """Own exactly one root transaction; only stamp after all locked checks.

    ``policy`` is loaded by the local CLI from host configuration, never from a
    web request. A failed commit is not retried: inspect read-only to resolve it.
    """
    serialize_adoption_approval(approval)
    serialize_trusted_target_policy(policy)
    if connection.dialect.name != "postgresql" or connection.in_transaction():
        raise ValueError("adoption requires an idle PostgreSQL connection")

    def result(status: str, error_code: str | None = None) -> AdoptionResult:
        return AdoptionResult(
            status, approval.approval_id, approval.operator_reference, error_code,
            HEAD_REVISION if status in {"adopted", "already_managed"} else None,
        )

    transaction = connection.begin()
    commit_attempted = False
    try:
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
        connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, public")
        # Lock before the first SELECT pins the repeatable-read snapshot. NOWAIT
        # also serializes simultaneous adopters without waiting on stale state.
        for name in sorted(APPLICATION_TABLES):
            connection.exec_driver_sql(
                f'LOCK TABLE public."{name}" IN SHARE ROW EXCLUSIVE MODE NOWAIT'
            )
        locked = connection.exec_driver_sql(
            f"SELECT pg_catalog.pg_try_advisory_xact_lock({ADVISORY_NAMESPACE}, {ADVISORY_PROTOCOL})"
        ).scalar_one()
        if not locked:
            transaction.rollback()
            return result("rejected", "adoption_busy")

        identity = observe_target_identity(connection)
        report = preflight_legacy_schema_in_transaction(connection, read_only=False)
        now = postgres_now(connection)
        observed = {
            "observed_server_identity_sha256": identity["server_identity_sha256"],
            "observed_database_identity_sha256": identity["database_identity_sha256"],
        }
        if report.status is PreflightStatus.ALREADY_MANAGED:
            validate_adoption_approval(approval, policy, server_now=now)
            verify_observed_target(policy, **observed)
            # The old preflight digest necessarily changes after a stamp. Never
            # stamp again, but still require an exact current base/head schema.
            transaction.rollback()
            return result("already_managed")

        validate_adoption_contract(approval, policy, report, server_now=now, **observed)
        _stamp(connection)
        _verify_stamp(connection)
        validate_adoption_approval(approval, policy, server_now=postgres_now(connection))
        commit_attempted = True
        transaction.commit()
        return result("adopted")
    except Exception as exc:
        try:
            if transaction.is_active:
                transaction.rollback()
        except Exception:
            return result("outcome_unknown", "rollback_failed")
        if commit_attempted:
            return result("outcome_unknown", "commit_outcome_unknown")
        return result("rejected", _error_code(exc))
