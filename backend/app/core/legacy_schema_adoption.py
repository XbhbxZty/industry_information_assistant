"""Pure contracts for controlled legacy-schema adoption.

This module models an operator's approval separately from the deployment-owned
target policy that a future execution layer must load from protected
configuration.  In particular, backup and maintenance-window fields are only
operator attestations; this module neither contacts external systems nor claims
they prove recoverability or writer exclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
import uuid
from typing import Any, Mapping

from .legacy_schema_preflight import (
    BASE_MANIFEST_ID,
    LegacyPreflightReport,
    PreflightStatus,
)


APPROVAL_FORMAT = "legacy-schema-adoption-approval/v1"
TRUSTED_TARGET_POLICY_FORMAT = "legacy-schema-trusted-target-policy/v1"
CONFIRMATION_PHRASE = "I CONFIRM LEGACY SCHEMA ADOPTION"
MAX_APPROVAL_TTL = timedelta(minutes=15)
ADOPTABLE_PROFILE_ID = BASE_MANIFEST_ID
SERVER_IDENTITY_FORMAT = "postgresql-server-identity/v1"
DATABASE_IDENTITY_FORMAT = "postgresql-database-identity/v1"

_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@()+ -]{0,255}\Z")
_SYSTEM_IDENTIFIER_RE = re.compile(r"[0-9]{1,20}\Z")
_SERVER_VERSION_NUM_RE = re.compile(r"[0-9]{5,6}\Z")
_UTC_MICROSECOND_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_APPROVAL_KEYS = frozenset({
    "format",
    "approval_id",
    "operator_reference",
    "profile_id",
    "target_policy_id",
    "expected_target_policy_sha256",
    "expected_preflight_sha256",
    "operator_attested_backup_reference",
    "operator_attested_maintenance_window_reference",
    "confirmation_phrase",
    "confirmed_at",
    "expires_at",
})
_POLICY_KEYS = frozenset({
    "format",
    "policy_id",
    "server_identity_sha256",
    "database_identity_sha256",
    "environment_id",
})


class AdoptionValidationCode(str, Enum):
    """Stable machine-readable rejections for the pure adoption contract."""

    INVALID_JSON = "invalid_json"
    INVALID_SHAPE = "invalid_shape"
    INVALID_VALUE = "invalid_value"
    INVALID_TIMESTAMP = "invalid_timestamp"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_FUTURE = "approval_future"
    APPROVAL_TTL_EXCEEDED = "approval_ttl_exceeded"
    TARGET_POLICY_MISMATCH = "target_policy_mismatch"
    TARGET_UNVERIFIABLE = "target_unverifiable"
    PREFLIGHT_MISMATCH = "preflight_mismatch"


class LegacySchemaAdoptionError(ValueError):
    """A strict contract failure, carrying a stable public classification."""

    def __init__(self, code: AdoptionValidationCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrustedTargetPolicy:
    """Deployment-owned expected target data, never nested in an approval."""

    policy_id: str
    server_identity_sha256: str
    database_identity_sha256: str
    environment_id: str


@dataclass(frozen=True)
class AdoptionApproval:
    """An immutable operator attestation, not an execution authorization alone."""

    approval_id: str
    operator_reference: str
    profile_id: str
    target_policy_id: str
    expected_target_policy_sha256: str
    expected_preflight_sha256: str
    operator_attested_backup_reference: str
    operator_attested_maintenance_window_reference: str
    confirmation_phrase: str
    confirmed_at: str
    expires_at: str


def load_trusted_target_policy(raw: str) -> TrustedTargetPolicy:
    """Parse a strict JSON policy without duplicate keys or coercion."""
    value = _strict_json_object(raw)
    return trusted_target_policy_from_mapping(value)


def load_adoption_approval(raw: str) -> AdoptionApproval:
    """Parse a strict JSON approval without duplicate keys or coercion."""
    value = _strict_json_object(raw)
    return adoption_approval_from_mapping(value)


def trusted_target_policy_from_mapping(value: Mapping[str, Any]) -> TrustedTargetPolicy:
    """Validate an independent deployment policy from a JSON-object mapping."""
    _require_exact_keys(value, _POLICY_KEYS, "trusted target policy")
    if value["format"] != TRUSTED_TARGET_POLICY_FORMAT:
        _fail(AdoptionValidationCode.INVALID_VALUE, "trusted target policy format is unsupported")
    return TrustedTargetPolicy(
        policy_id=_require_uuid(value["policy_id"], "policy_id"),
        server_identity_sha256=_require_digest(value["server_identity_sha256"], "server_identity_sha256"),
        database_identity_sha256=_require_digest(
            value["database_identity_sha256"], "database_identity_sha256",
        ),
        environment_id=_require_identifier(value["environment_id"], "environment_id"),
    )


def adoption_approval_from_mapping(value: Mapping[str, Any]) -> AdoptionApproval:
    """Validate an approval bound to both target policy ID and content digest."""
    _require_exact_keys(value, _APPROVAL_KEYS, "adoption approval")
    if value["format"] != APPROVAL_FORMAT:
        _fail(AdoptionValidationCode.INVALID_VALUE, "adoption approval format is unsupported")
    if value["profile_id"] != ADOPTABLE_PROFILE_ID:
        _fail(AdoptionValidationCode.INVALID_VALUE, "adoption profile is unsupported")
    if value["confirmation_phrase"] != CONFIRMATION_PHRASE:
        _fail(AdoptionValidationCode.CONFIRMATION_MISMATCH, "confirmation phrase does not match")
    approval = AdoptionApproval(
        approval_id=_require_uuid(value["approval_id"], "approval_id"),
        operator_reference=_require_identifier(value["operator_reference"], "operator_reference"),
        profile_id=value["profile_id"],
        target_policy_id=_require_uuid(value["target_policy_id"], "target_policy_id"),
        expected_target_policy_sha256=_require_digest(
            value["expected_target_policy_sha256"], "expected_target_policy_sha256",
        ),
        expected_preflight_sha256=_require_digest(
            value["expected_preflight_sha256"], "expected_preflight_sha256",
        ),
        operator_attested_backup_reference=_require_attested_reference(
            value["operator_attested_backup_reference"], "operator_attested_backup_reference",
        ),
        operator_attested_maintenance_window_reference=_require_attested_reference(
            value["operator_attested_maintenance_window_reference"],
            "operator_attested_maintenance_window_reference",
        ),
        confirmation_phrase=value["confirmation_phrase"],
        confirmed_at=_require_utc_timestamp(value["confirmed_at"], "confirmed_at"),
        expires_at=_require_utc_timestamp(value["expires_at"], "expires_at"),
    )
    _validate_approval_timestamps(approval, now=None)
    return approval


def serialize_trusted_target_policy(policy: TrustedTargetPolicy) -> str:
    """Return compact canonical JSON after revalidating direct construction."""
    return _serialize(_policy_mapping(policy))


def serialize_adoption_approval(approval: AdoptionApproval) -> str:
    """Return compact canonical JSON; wording retains the attestation boundary."""
    mapping = _approval_mapping(approval)
    _validate_approval_timestamps(approval, now=None)
    return _serialize(mapping)


def trusted_target_policy_sha256(policy: TrustedTargetPolicy) -> str:
    """Bind an approval to immutable policy content as well as its stable ID."""
    return hashlib.sha256(serialize_trusted_target_policy(policy).encode("ascii")).hexdigest()


def compute_server_identity_sha256(
    *, system_identifier: str, server_address: str, server_port: int,
    server_version_num: str,
) -> str:
    """Canonicalize the writer-observed PostgreSQL cluster identity."""
    payload = {
        "format": SERVER_IDENTITY_FORMAT,
        "system_identifier": _require_system_identifier(system_identifier),
        "server_address": _require_visible_text(server_address, "server_address", 255),
        "server_port": _require_port(server_port),
        "server_version_num": _require_server_version_num(server_version_num),
    }
    return _mapping_sha256(payload)


def compute_database_identity_sha256(
    *, database_name: str, database_oid: int, database_owner: str,
) -> str:
    """Canonicalize the logical database identity without returning raw names."""
    payload = {
        "format": DATABASE_IDENTITY_FORMAT,
        "database_name": _require_postgres_name(database_name, "database_name"),
        "database_oid": _require_oid(database_oid),
        "database_owner": _require_postgres_name(database_owner, "database_owner"),
    }
    return _mapping_sha256(payload)


def validate_adoption_approval(
    approval: AdoptionApproval,
    policy: TrustedTargetPolicy,
    *,
    server_now: datetime,
) -> None:
    """Validate consent lifetime and its reference to an independent policy.

    ``server_now`` is intentionally supplied by the future execution layer's
    trusted clock.  Client-provided time is never used to decide expiry.
    """
    _policy_mapping(policy)
    _approval_mapping(approval)
    if approval.target_policy_id != policy.policy_id:
        _fail(AdoptionValidationCode.TARGET_POLICY_MISMATCH, "approval targets another policy")
    if approval.expected_target_policy_sha256 != trusted_target_policy_sha256(policy):
        _fail(AdoptionValidationCode.TARGET_POLICY_MISMATCH, "target policy content has changed")
    _validate_approval_timestamps(approval, now=_require_server_now(server_now))


def verify_observed_target(
    policy: TrustedTargetPolicy,
    *,
    observed_server_identity_sha256: str,
    observed_database_identity_sha256: str,
) -> None:
    """Compare execution-layer observations to a protected target policy.

    This is deliberately not a database call.  A caller that has no independent
    binding value must pass nothing useful and is rejected rather than being
    allowed to turn its own declaration into a "verified" target.
    """
    _policy_mapping(policy)
    observed_server = _require_digest(observed_server_identity_sha256, "observed_server_identity_sha256")
    observed_database = _require_digest(
        observed_database_identity_sha256, "observed_database_identity_sha256",
    )
    if (
        observed_server != policy.server_identity_sha256
        or observed_database != policy.database_identity_sha256
    ):
        _fail(AdoptionValidationCode.TARGET_UNVERIFIABLE, "observed target does not match protected policy")


def validate_approved_preflight(
    approval: AdoptionApproval,
    report: LegacyPreflightReport,
) -> None:
    """Match approval to a freshly observed, exactly adoptable preflight.

    This pure function cannot establish report provenance.  The execution layer
    must pass the report it obtained on its writer connection after acquiring
    the adoption locks; accepting a client-supplied report would void the
    second-observation guarantee.
    """
    _approval_mapping(approval)
    if not isinstance(report, LegacyPreflightReport):
        _fail(AdoptionValidationCode.INVALID_VALUE, "report must be LegacyPreflightReport")
    _require_digest(report.snapshot_sha256, "report.snapshot_sha256")
    observed_digest = _require_digest(report.preflight_sha256, "report.preflight_sha256")
    if (
        report.status is not PreflightStatus.EXACT_ADOPTABLE
        or report.profile_id != ADOPTABLE_PROFILE_ID
        or report.unmanaged_packages
        or report.differences
        or approval.profile_id != report.profile_id
        or approval.expected_preflight_sha256 != observed_digest
    ):
        _fail(
            AdoptionValidationCode.PREFLIGHT_MISMATCH,
            "approved preflight does not match the current adoptable schema",
        )


def validate_adoption_contract(
    approval: AdoptionApproval,
    policy: TrustedTargetPolicy,
    report: LegacyPreflightReport,
    *,
    server_now: datetime,
    observed_server_identity_sha256: str,
    observed_database_identity_sha256: str,
) -> None:
    """Apply every pure adoption gate through one non-optional entry point.

    The execution layer remains responsible for authenticating the operator,
    loading ``policy`` from protected deployment configuration, obtaining
    ``server_now`` from PostgreSQL, and producing ``report`` plus both observed
    identities on the locked writer connection.
    """
    validate_adoption_approval(approval, policy, server_now=server_now)
    verify_observed_target(
        policy,
        observed_server_identity_sha256=observed_server_identity_sha256,
        observed_database_identity_sha256=observed_database_identity_sha256,
    )
    validate_approved_preflight(approval, report)


def _strict_json_object(raw: str) -> Mapping[str, Any]:
    if not isinstance(raw, str):
        _fail(AdoptionValidationCode.INVALID_JSON, "JSON input must be a string")

    def reject_constant(value: str) -> None:
        _fail(AdoptionValidationCode.INVALID_JSON, f"JSON constant {value!r} is not permitted")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(AdoptionValidationCode.INVALID_JSON, "duplicate JSON object key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except LegacySchemaAdoptionError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LegacySchemaAdoptionError(AdoptionValidationCode.INVALID_JSON, "invalid JSON input") from exc
    if type(value) is not dict:
        _fail(AdoptionValidationCode.INVALID_SHAPE, "JSON root must be an object")
    return value


def _serialize(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _policy_mapping(policy: TrustedTargetPolicy) -> dict[str, str]:
    if not isinstance(policy, TrustedTargetPolicy):
        _fail(AdoptionValidationCode.INVALID_VALUE, "policy must be TrustedTargetPolicy")
    return {
        "format": TRUSTED_TARGET_POLICY_FORMAT,
        "policy_id": _require_uuid(policy.policy_id, "policy_id"),
        "server_identity_sha256": _require_digest(policy.server_identity_sha256, "server_identity_sha256"),
        "database_identity_sha256": _require_digest(
            policy.database_identity_sha256, "database_identity_sha256",
        ),
        "environment_id": _require_identifier(policy.environment_id, "environment_id"),
    }


def _approval_mapping(approval: AdoptionApproval) -> dict[str, str]:
    if not isinstance(approval, AdoptionApproval):
        _fail(AdoptionValidationCode.INVALID_VALUE, "approval must be AdoptionApproval")
    if approval.profile_id != ADOPTABLE_PROFILE_ID:
        _fail(AdoptionValidationCode.INVALID_VALUE, "adoption profile is unsupported")
    if approval.confirmation_phrase != CONFIRMATION_PHRASE:
        _fail(AdoptionValidationCode.CONFIRMATION_MISMATCH, "confirmation phrase does not match")
    return {
        "format": APPROVAL_FORMAT,
        "approval_id": _require_uuid(approval.approval_id, "approval_id"),
        "operator_reference": _require_identifier(
            approval.operator_reference, "operator_reference",
        ),
        "profile_id": approval.profile_id,
        "target_policy_id": _require_uuid(approval.target_policy_id, "target_policy_id"),
        "expected_target_policy_sha256": _require_digest(
            approval.expected_target_policy_sha256, "expected_target_policy_sha256",
        ),
        "expected_preflight_sha256": _require_digest(
            approval.expected_preflight_sha256, "expected_preflight_sha256",
        ),
        "operator_attested_backup_reference": _require_attested_reference(
            approval.operator_attested_backup_reference, "operator_attested_backup_reference",
        ),
        "operator_attested_maintenance_window_reference": _require_attested_reference(
            approval.operator_attested_maintenance_window_reference,
            "operator_attested_maintenance_window_reference",
        ),
        "confirmation_phrase": approval.confirmation_phrase,
        "confirmed_at": _require_utc_timestamp(approval.confirmed_at, "confirmed_at"),
        "expires_at": _require_utc_timestamp(approval.expires_at, "expires_at"),
    }


def _validate_approval_timestamps(approval: AdoptionApproval, *, now: datetime | None) -> None:
    confirmed_at = _parse_utc_timestamp(approval.confirmed_at, "confirmed_at")
    expires_at = _parse_utc_timestamp(approval.expires_at, "expires_at")
    if expires_at <= confirmed_at:
        _fail(AdoptionValidationCode.INVALID_TIMESTAMP, "expires_at must be after confirmed_at")
    if expires_at - confirmed_at > MAX_APPROVAL_TTL:
        _fail(AdoptionValidationCode.APPROVAL_TTL_EXCEEDED, "approval TTL exceeds the maximum")
    if now is None:
        return
    if confirmed_at > now:
        _fail(AdoptionValidationCode.APPROVAL_FUTURE, "approval confirmation time is in the future")
    if now >= expires_at:
        _fail(AdoptionValidationCode.APPROVAL_EXPIRED, "approval has expired")


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != expected:
        _fail(AdoptionValidationCode.INVALID_SHAPE, f"{label} has unexpected fields")
    return value


def _require_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise LegacySchemaAdoptionError(
            AdoptionValidationCode.INVALID_VALUE, f"{label} must be a canonical UUID",
        ) from exc
    if str(parsed) != value:
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} must be lowercase hyphenated UUID")
    return value


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX_SHA256_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} is invalid")
    return value


def _require_attested_reference(value: Any, label: str) -> str:
    if not isinstance(value, str) or _REFERENCE_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} must be a normalized operator attestation")
    return value


def _require_utc_timestamp(value: Any, label: str) -> str:
    _parse_utc_timestamp(value, label)
    return value


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or _UTC_MICROSECOND_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_TIMESTAMP, f"{label} must be a UTC microsecond RFC3339 timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise LegacySchemaAdoptionError(
            AdoptionValidationCode.INVALID_TIMESTAMP, f"{label} is invalid",
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _require_server_now(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        _fail(AdoptionValidationCode.INVALID_TIMESTAMP, "server_now must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_serialize(value).encode("ascii")).hexdigest()


def _require_system_identifier(value: Any) -> str:
    if not isinstance(value, str) or _SYSTEM_IDENTIFIER_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_VALUE, "system_identifier is invalid")
    return value


def _require_server_version_num(value: Any) -> str:
    if not isinstance(value, str) or _SERVER_VERSION_NUM_RE.fullmatch(value) is None:
        _fail(AdoptionValidationCode.INVALID_VALUE, "server_version_num is invalid")
    return value


def _require_visible_text(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} is invalid")
    return value


def _require_port(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        _fail(AdoptionValidationCode.INVALID_VALUE, "server_port is invalid")
    return value


def _require_oid(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 4294967295:
        _fail(AdoptionValidationCode.INVALID_VALUE, "database_oid is invalid")
    return value


def _require_postgres_name(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or len(value.encode("utf-8")) > 63
    ):
        _fail(AdoptionValidationCode.INVALID_VALUE, f"{label} is invalid")
    return value


def _fail(code: AdoptionValidationCode, message: str) -> None:
    raise LegacySchemaAdoptionError(code, message)
