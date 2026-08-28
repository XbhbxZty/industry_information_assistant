"""Canonical, agent-visible contracts for fictional factoring simulation cases.

The loader intentionally owns only the ``input/`` trust zone.  It has no
oracle import, no oracle path field, and never scans a package directory.  The
only files it can open are the explicit input files in the canonical manifest.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "simulation-factoring-case-v1"


class CasePackageError(ValueError):
    """A case package cannot safely be loaded."""


class CaseValidationError(CasePackageError):
    """Input data violates the canonical factoring simulation contract."""


class SourceOutcome(str, Enum):
    SUCCESS_WITH_RECORDS = "success_with_records"
    SUCCESS_NO_RECORD = "success_no_record"
    SUBJECT_NOT_FOUND = "subject_not_found"
    NOT_QUERIED = "not_queried"
    NOT_PROVIDED = "not_provided"
    UNAUTHORIZED = "unauthorized"
    TIMEOUT = "timeout"
    SOURCE_UNAVAILABLE = "source_unavailable"


class CaseRole(str, Enum):
    APPLICANT_SUPPLIER = "applicant_supplier"
    PRIMARY_BORROWER = "primary_borrower"
    ACCOUNT_DEBTOR = "account_debtor"
    GUARANTOR = "guarantor"
    SHAREHOLDER = "shareholder"
    ACTUAL_CONTROLLER = "actual_controller"
    RELATED_PARTY = "related_party"
    CONTRACT_PARTY = "contract_party"
    INVOICE_ISSUER = "invoice_issuer"
    PAYER = "payer"


class EntityKind(str, Enum):
    ENTERPRISE = "enterprise"
    INDIVIDUAL = "individual"


class MeasurementUnit(str, Enum):
    CNY = "CNY"
    CNY_TEN_THOUSAND = "CNY_10K"
    CNY_MILLION = "CNY_MILLION"
    PERCENT = "PERCENT"
    DAYS = "DAYS"
    COUNT = "COUNT"


class SourceChannel(str, Enum):
    PUBLIC = "public"
    AUTHORIZED = "authorized"
    ENTERPRISE_SUBMITTED = "enterprise_submitted"
    SITE_VISIT = "site_visit"


class RelationshipType(str, Enum):
    CONTROLS = "controls"
    SHAREHOLDER_OF = "shareholder_of"
    GUARANTEES = "guarantees"
    ACCOUNT_DEBTOR_OF = "account_debtor_of"
    CONTRACT_COUNTERPARTY = "contract_counterparty"
    RELATED_PARTY = "related_party"


class _StrictModel(BaseModel):
    """Forbid unknown fields and wire coercion; freeze public model fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_RESERVED_RECORD_KEY_FRAGMENTS = (
    "oracle",
    "expected_decision",
    "expected_verdict",
    "verdict",
    "score",
    "verified",
    "verification_status",
    "decision",
)


def _require_identifier(value: str, field_name: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must be 1-128 ASCII letters/digits, '.', '_' or '-'"
        )
    return value


def is_valid_unified_credit_code(value: str) -> bool:
    """Return whether *value* passes the Chinese 18-character USCC checksum."""

    normalized = value.strip().upper()
    if (
        len(normalized) != 18
        or normalized[0] not in {"1", "5", "9", "Y"}
        or any(char not in _USCC_CHARS for char in normalized)
    ):
        return False
    total = sum(
        _USCC_CHARS.index(char) * weight
        for char, weight in zip(normalized[:17], _USCC_WEIGHTS)
    )
    return _USCC_CHARS[(31 - total % 31) % 31] == normalized[17]


def _safe_input_relative_path(value: str, field_name: str) -> str:
    """Validate a portable relative path without ever normalising ``..`` away."""

    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "\\" in value:
        raise ValueError(f"{field_name} must use '/' separators")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"{field_name} must be a relative path under input/")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError(f"{field_name} may not contain '.' or '..' path segments")
    if not posix.parts or posix.parts[0] != "input":
        raise ValueError(f"{field_name} must be located under input/")
    return posix.as_posix()


def _safe_material_relative_path(value: str) -> str:
    normalized = _safe_input_relative_path(value, "relative_path")
    parts = PurePosixPath(normalized).parts
    if len(parts) < 3 or parts[1] not in {"documents", "site_visit"}:
        raise ValueError(
            "relative_path must be under input/documents/ or input/site_visit/"
        )
    return normalized


def _freeze_and_validate_record(value: Any, path: str = "records") -> Any:
    """Reject hidden evaluation labels and return recursively immutable JSON data."""

    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized = raw_key.lower().replace("-", "_")
            if any(fragment in normalized for fragment in _RESERVED_RECORD_KEY_FRAGMENTS):
                raise ValueError(f"{path}.{raw_key} is a reserved evaluation-only key")
            frozen[raw_key] = _freeze_and_validate_record(item, f"{path}.{raw_key}")
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze_and_validate_record(item, f"{path}[]") for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_and_validate_record(item, f"{path}[]") for item in value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    raise ValueError(f"{path} must contain JSON-compatible values only")


def _defensive_json_copy(value: Any) -> Any:
    """Turn an immutable record back into ordinary JSON for serialisation only."""

    if isinstance(value, Mapping):
        return {key: _defensive_json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_defensive_json_copy(item) for item in value]
    return value


class ReportingPeriod(_StrictModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _validate_range(self) -> "ReportingPeriod":
        if self.end_date < self.start_date:
            raise ValueError("reporting period end_date must not precede start_date")
        return self


class Measurement(_StrictModel):
    """A generic measured assertion; zero and negative values are valid evidence."""

    value: Decimal = Field(strict=False)
    unit: MeasurementUnit
    period: ReportingPeriod | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _reject_numeric_strings(cls, value: Any) -> Any:
        # JSON numeric tokens are accepted; quoted numerics are wire coercion
        # and can silently turn malformed evidence into a measured fact.
        if isinstance(value, str):
            raise ValueError("measurement value must be a JSON number, not a string")
        return value


class LoanApplication(_StrictModel):
    application_id: str
    application_date: date
    primary_borrower_id: str
    applicant_supplier_id: str
    debtor_entity_id: str
    requested_amount: Measurement
    requested_term: Measurement
    financing_purpose: str = Field(min_length=1, max_length=1024)
    receivable_face_amount: Measurement
    advance_rate: Measurement
    contract_number: str = Field(min_length=1, max_length=256)
    payment_due_date: date
    trade_period: ReportingPeriod

    @field_validator(
        "application_id",
        "primary_borrower_id",
        "applicant_supplier_id",
        "debtor_entity_id",
    )
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_factoring_terms(self) -> "LoanApplication":
        for label, measurement in (
            ("requested_amount", self.requested_amount),
            ("receivable_face_amount", self.receivable_face_amount),
        ):
            if measurement.unit not in {
                MeasurementUnit.CNY,
                MeasurementUnit.CNY_TEN_THOUSAND,
                MeasurementUnit.CNY_MILLION,
            } or measurement.value <= 0:
                raise ValueError(f"{label} must be a positive CNY-denominated measurement")
        if self.requested_term.unit is not MeasurementUnit.DAYS or self.requested_term.value <= 0:
            raise ValueError("requested_term must be a positive DAYS measurement")
        if (
            self.advance_rate.unit is not MeasurementUnit.PERCENT
            or self.advance_rate.value <= 0
            or self.advance_rate.value > 100
        ):
            raise ValueError("advance_rate must be a PERCENT measurement in (0, 100]")
        if self.trade_period.end_date > self.payment_due_date:
            raise ValueError("payment_due_date must not precede the trade period end_date")
        return self


class CaseEntity(_StrictModel):
    entity_id: str
    name: str = Field(min_length=1, max_length=256)
    entity_kind: EntityKind = EntityKind.ENTERPRISE
    unified_credit_code: str | None = None
    roles: frozenset[CaseRole] = Field(min_length=1)

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_id(cls, value: str) -> str:
        return _require_identifier(value, "entity_id")

    @field_validator("unified_credit_code")
    @classmethod
    def _normalize_credit_code(cls, value: str | None) -> str | None:
        return None if value is None else value.upper()

    @model_validator(mode="after")
    def _validate_credit_code_for_kind(self) -> "CaseEntity":
        if self.entity_kind is EntityKind.ENTERPRISE:
            if not self.unified_credit_code:
                raise ValueError("an enterprise requires unified_credit_code")
            if not is_valid_unified_credit_code(self.unified_credit_code):
                raise ValueError("unified_credit_code is not a valid 18-character USCC")
        elif self.unified_credit_code is not None:
            raise ValueError("an individual must not declare unified_credit_code")
        return self


class EntityRelationship(_StrictModel):
    relationship_id: str
    from_entity_id: str
    to_entity_id: str
    relationship_type: RelationshipType
    as_of_date: date
    percent: Measurement | None = None
    supporting_material_ids: tuple[str, ...] = ()

    @field_validator("relationship_id", "from_entity_id", "to_entity_id")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @field_validator("supporting_material_ids")
    @classmethod
    def _validate_material_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(
            _require_identifier(item, "supporting_material_ids item") for item in value
        )
        if len(validated) != len(set(validated)):
            raise ValueError("supporting_material_ids must not contain duplicates")
        return validated

    @model_validator(mode="after")
    def _validate_relationship_measurement(self) -> "EntityRelationship":
        if self.from_entity_id == self.to_entity_id:
            raise ValueError("a relationship must connect two distinct entities")
        if self.percent is not None and (
            self.percent.unit is not MeasurementUnit.PERCENT
            or self.percent.value < 0
            or self.percent.value > 100
        ):
            raise ValueError("relationship percent must be a PERCENT measurement in [0, 100]")
        return self


class EntityBundle(_StrictModel):
    entities: tuple[CaseEntity, ...] = Field(min_length=1)
    relationships: tuple[EntityRelationship, ...] = ()


class EnterpriseClaim(_StrictModel):
    """A claimed assertion only; it has no verified state or verdict field."""

    claim_id: str
    field_id: str
    subject_entity_id: str
    status: Literal["claimed"] = "claimed"
    asserted_by: str = Field(min_length=1, max_length=256)
    as_of_date: date
    assertion: str | None = Field(default=None, min_length=1, max_length=4096)
    measurement: Measurement | None = None
    supporting_material_ids: tuple[str, ...] = ()

    @field_validator("claim_id", "field_id", "subject_entity_id")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @field_validator("supporting_material_ids")
    @classmethod
    def _validate_material_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(
            _require_identifier(item, "supporting_material_ids item") for item in value
        )
        if len(validated) != len(set(validated)):
            raise ValueError("supporting_material_ids must not contain duplicates")
        return validated

    @model_validator(mode="after")
    def _require_exactly_one_claim_shape(self) -> "EnterpriseClaim":
        if (self.assertion is None) == (self.measurement is None):
            raise ValueError("a claim requires exactly one of assertion or measurement")
        return self


class SourceDescriptor(_StrictModel):
    source_id: str
    source_channel: SourceChannel
    source_type: str = Field(min_length=1, max_length=128)
    issuer: str = Field(min_length=1, max_length=256)
    subject_entity_id: str
    query_scope: str = Field(min_length=1, max_length=2048)

    @field_validator("source_id", "subject_entity_id")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)


class SourceQueryResult(_StrictModel):
    source_id: str
    outcome: SourceOutcome
    queried_field_ids: tuple[str, ...] = Field(min_length=1)
    retrieved_at: datetime | None = None
    as_of_date: date | None = None
    publication_date: date | None = None
    observed_at: date | None = None
    records: tuple[Mapping[str, Any], ...] = Field(default=(), strict=False)
    outcome_detail: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("source_id")
    @classmethod
    def _validate_source_id(cls, value: str) -> str:
        return _require_identifier(value, "source_id")

    @field_validator("retrieved_at")
    @classmethod
    def _require_retrieval_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("retrieved_at must include an explicit timezone offset")
        return value

    @field_validator("queried_field_ids")
    @classmethod
    def _validate_field_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(_require_identifier(item, "queried_field_ids item") for item in value)
        if len(validated) != len(set(validated)):
            raise ValueError("queried_field_ids must not contain duplicates")
        return validated

    @field_validator("records", mode="before")
    @classmethod
    def _reject_reserved_record_keys(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("records must be an array")
        # ``MappingProxyType`` is produced only after pydantic's normal parsing.
        for index, item in enumerate(value):
            _freeze_and_validate_record(item, f"records[{index}]")
        return value

    @field_validator("records", mode="after")
    @classmethod
    def _freeze_records(cls, value: tuple[Mapping[str, Any], ...]) -> tuple[Mapping[str, Any], ...]:
        return tuple(_freeze_and_validate_record(item) for item in value)

    @field_serializer("records")
    def _serialize_records(self, value: tuple[Mapping[str, Any], ...]) -> list[Any]:
        return [_defensive_json_copy(item) for item in value]

    @model_validator(mode="after")
    def _enforce_outcome_invariants(self) -> "SourceQueryResult":
        if self.outcome is SourceOutcome.SUCCESS_WITH_RECORDS:
            if not self.records:
                raise ValueError("success_with_records requires at least one record")
            if self.outcome_detail is not None:
                raise ValueError("a successful result must not carry outcome_detail")
            if self.retrieved_at is None:
                raise ValueError("success_with_records requires retrieved_at")
            if (
                self.as_of_date is None
                and self.publication_date is None
                and self.observed_at is None
            ):
                raise ValueError(
                    "success_with_records requires as_of_date, publication_date, or observed_at"
                )
        elif self.outcome is SourceOutcome.SUCCESS_NO_RECORD:
            if self.records:
                raise ValueError("success_no_record must not carry records")
            if self.outcome_detail is not None:
                raise ValueError("a successful result must not carry outcome_detail")
            if self.retrieved_at is None or self.observed_at is None:
                raise ValueError("success_no_record requires retrieved_at and observed_at")
        else:
            if self.records:
                raise ValueError(f"{self.outcome.value} must not carry records")
            if self.outcome_detail is None:
                raise ValueError(f"{self.outcome.value} requires outcome_detail")
        return self

    @property
    def is_success(self) -> bool:
        return self.outcome in {
            SourceOutcome.SUCCESS_WITH_RECORDS,
            SourceOutcome.SUCCESS_NO_RECORD,
        }

    @property
    def establishes_no_record(self) -> bool:
        """The sole query state allowed to support a scoped negative conclusion."""
        return self.outcome is SourceOutcome.SUCCESS_NO_RECORD


class MaterialDescriptor(_StrictModel):
    material_id: str
    subject_entity_id: str
    material_type: str = Field(min_length=1, max_length=128)
    relative_path: str
    provenance: str = Field(min_length=1, max_length=1024)
    source_channel: SourceChannel
    issuer: str = Field(min_length=1, max_length=256)
    document_date: date | None = None
    publication_date: date | None = None
    observed_at: date | None = None
    reporting_period: ReportingPeriod | None = None
    date_unknown_reason: str | None = Field(default=None, min_length=1, max_length=1024)
    sha256: str | None = None

    @field_validator("material_id", "subject_entity_id")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @field_validator("relative_path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _safe_material_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")
        return normalized

    @model_validator(mode="after")
    def _require_explicit_factual_date_state(self) -> "MaterialDescriptor":
        has_factual_date = any(
            value is not None
            for value in (
                self.document_date,
                self.publication_date,
                self.observed_at,
                self.reporting_period,
            )
        )
        if not has_factual_date and self.date_unknown_reason is None:
            raise ValueError(
                "material requires a factual date/period or date_unknown_reason"
            )
        if has_factual_date and self.date_unknown_reason is not None:
            raise ValueError(
                "date_unknown_reason is allowed only when all factual dates are unknown"
            )
        return self


class InputFileSet(_StrictModel):
    """The complete and only agent-visible input surface for a package."""

    application: str
    entities: str
    claims: str
    sources: str
    materials: str

    @field_validator("application", "entities", "claims", "sources", "materials")
    @classmethod
    def _validate_paths(cls, value: str, info: Any) -> str:
        return _safe_input_relative_path(value, info.field_name)

    @model_validator(mode="after")
    def _require_distinct_files(self) -> "InputFileSet":
        paths = (self.application, self.entities, self.claims, self.sources, self.materials)
        if len(set(paths)) != len(paths):
            raise ValueError("each declared input contract must use a distinct file")
        return self


class DueDiligenceManifest(_StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    case_id: str
    title: str = Field(min_length=1, max_length=256)
    simulation_only: Literal[True]
    fictional_notice: str = Field(min_length=1, max_length=2048)
    as_of: date
    cutoff_date: date
    business_type: str = Field(min_length=1, max_length=256)
    scenario: str = Field(min_length=1, max_length=4096)
    primary_subject_id: str
    input: InputFileSet

    @field_validator("case_id", "primary_subject_id")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_as_of_and_cutoff(self) -> "DueDiligenceManifest":
        if self.cutoff_date > self.as_of:
            raise ValueError("cutoff_date must not be after as_of")
        return self


class SourceBundle(_StrictModel):
    sources: tuple[SourceDescriptor, ...]
    query_results: tuple[SourceQueryResult, ...]


class DueDiligenceCase(_StrictModel):
    """Fully validated production input.  It deliberately has no oracle field."""

    manifest: DueDiligenceManifest
    loan_application: LoanApplication
    entities: tuple[CaseEntity, ...] = Field(min_length=1)
    relationships: tuple[EntityRelationship, ...] = ()
    claims: tuple[EnterpriseClaim, ...] = ()
    sources: tuple[SourceDescriptor, ...] = ()
    query_results: tuple[SourceQueryResult, ...] = ()
    materials: tuple[MaterialDescriptor, ...] = ()

    @model_validator(mode="after")
    def _validate_cross_file_contract(self) -> "DueDiligenceCase":
        def unique_ids(items: tuple[Any, ...], attr: str, label: str) -> set[str]:
            values = [getattr(item, attr) for item in items]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} ids are not allowed")
            return set(values)

        entity_ids = unique_ids(self.entities, "entity_id", "entity")
        credit_codes = [
            entity.unified_credit_code
            for entity in self.entities
            if entity.unified_credit_code is not None
        ]
        if len(credit_codes) != len(set(credit_codes)):
            raise ValueError("an enterprise unified_credit_code may appear only once")

        application = self.loan_application
        declared_primary_borrowers = {
            entity.entity_id
            for entity in self.entities
            if CaseRole.PRIMARY_BORROWER in entity.roles
        }
        if declared_primary_borrowers != {application.primary_borrower_id}:
            raise ValueError(
                "exactly the application's primary_borrower_id must carry the primary_borrower role"
            )
        if self.manifest.primary_subject_id != application.primary_borrower_id:
            raise ValueError(
                "manifest primary_subject_id must equal application's primary_borrower_id"
            )
        for field_name, entity_id, required_role in (
            ("primary_borrower_id", application.primary_borrower_id, CaseRole.PRIMARY_BORROWER),
            ("applicant_supplier_id", application.applicant_supplier_id, CaseRole.APPLICANT_SUPPLIER),
            ("debtor_entity_id", application.debtor_entity_id, CaseRole.ACCOUNT_DEBTOR),
            ("primary_subject_id", self.manifest.primary_subject_id, CaseRole.PRIMARY_BORROWER),
        ):
            if entity_id not in entity_ids:
                raise ValueError(f"{field_name} references an unknown entity")
            entity = next(item for item in self.entities if item.entity_id == entity_id)
            if required_role not in entity.roles:
                raise ValueError(f"{field_name} must reference an entity with role {required_role.value}")
        if application.application_date > self.manifest.cutoff_date:
            raise ValueError("application_date must not be after manifest cutoff_date")
        if application.trade_period.end_date > self.manifest.cutoff_date:
            raise ValueError("trade_period end_date must not be after manifest cutoff_date")

        material_ids = unique_ids(self.materials, "material_id", "material")
        material_paths = [material.relative_path for material in self.materials]
        if len(material_paths) != len(set(material_paths)):
            raise ValueError("each material must have a distinct relative_path")
        for material in self.materials:
            if material.subject_entity_id not in entity_ids:
                raise ValueError(f"material {material.material_id} references an unknown entity")
            _validate_factual_dates(
                material, self.manifest.cutoff_date, f"material {material.material_id}"
            )

        unique_ids(self.relationships, "relationship_id", "relationship")
        for relationship in self.relationships:
            if relationship.from_entity_id not in entity_ids:
                raise ValueError(
                    f"relationship {relationship.relationship_id} references an unknown from_entity"
                )
            if relationship.to_entity_id not in entity_ids:
                raise ValueError(
                    f"relationship {relationship.relationship_id} references an unknown to_entity"
                )
            if relationship.as_of_date > self.manifest.cutoff_date:
                raise ValueError(
                    f"relationship {relationship.relationship_id} as_of_date is after manifest cutoff_date"
                )
            dangling_materials = set(relationship.supporting_material_ids) - material_ids
            if dangling_materials:
                raise ValueError(
                    f"relationship {relationship.relationship_id} references unknown material ids: "
                    f"{sorted(dangling_materials)}"
                )

        unique_ids(self.claims, "claim_id", "claim")
        known_subject_fields: set[tuple[str, str]] = set()
        for claim in self.claims:
            if claim.subject_entity_id not in entity_ids:
                raise ValueError(f"claim {claim.claim_id} references an unknown entity")
            if claim.as_of_date > self.manifest.cutoff_date:
                raise ValueError(
                    f"claim {claim.claim_id} as_of_date is after manifest cutoff_date"
                )
            if claim.measurement and claim.measurement.period:
                if claim.measurement.period.end_date > self.manifest.cutoff_date:
                    raise ValueError(
                        f"claim {claim.claim_id} measurement period extends past manifest cutoff_date"
                    )
            dangling_materials = set(claim.supporting_material_ids) - material_ids
            if dangling_materials:
                raise ValueError(
                    f"claim {claim.claim_id} references unknown material ids: "
                    f"{sorted(dangling_materials)}"
                )
            known_subject_fields.add((claim.subject_entity_id, claim.field_id))

        source_ids = unique_ids(self.sources, "source_id", "source")
        result_ids = unique_ids(self.query_results, "source_id", "query result")
        if source_ids != result_ids:
            raise ValueError(
                "every declared source requires exactly one explicit outcome "
                f"(missing={sorted(source_ids - result_ids)}, "
                f"unknown={sorted(result_ids - source_ids)})"
            )
        result_by_source = {result.source_id: result for result in self.query_results}
        for source in self.sources:
            if source.subject_entity_id not in entity_ids:
                raise ValueError(f"source {source.source_id} references an unknown entity")
            result = result_by_source[source.source_id]
            for field_id in result.queried_field_ids:
                if (source.subject_entity_id, field_id) not in known_subject_fields:
                    raise ValueError(
                        f"source {source.source_id} queried unknown field_id {field_id} "
                        f"for subject {source.subject_entity_id}"
                    )
            # ``as_of`` is the overall simulation/evaluation time, while
            # ``cutoff_date`` is the evidence boundary. ``retrieved_at`` is
            # collection metadata and is intentionally never compared here.
            _validate_factual_dates(
                result, self.manifest.cutoff_date, f"source {source.source_id}"
            )
        return self


def _validate_factual_dates(item: Any, cutoff_date: date, label: str) -> None:
    for field_name in ("as_of_date", "publication_date", "observed_at", "document_date"):
        value = getattr(item, field_name, None)
        if value is not None and value > cutoff_date:
            raise ValueError(f"{label} {field_name} is after manifest cutoff_date")
    period = getattr(item, "reporting_period", None)
    if period is not None and period.end_date > cutoff_date:
        raise ValueError(f"{label} reporting_period extends past manifest cutoff_date")


def _package_root(case_root: str | Path) -> Path:
    root = Path(case_root).resolve(strict=True)
    if not root.is_dir():
        raise CasePackageError(f"case root is not a directory: {root}")
    return root


def _resolve_root_manifest(root: Path) -> Path:
    manifest = root / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise CasePackageError(f"case package has no regular root manifest.json: {root}")
    resolved = manifest.resolve(strict=True)
    if resolved.parent != root or not resolved.is_relative_to(root):
        raise CasePackageError("manifest.json must resolve directly inside case root")
    return resolved


def _resolve_declared_input(root: Path, relative_path: str) -> Path:
    """Resolve an input declaration without letting symlinks cross trust zones."""

    input_directory = root / "input"
    if input_directory.is_symlink() or not input_directory.is_dir():
        raise CasePackageError("input/ must be a real directory inside the case package")
    input_root = input_directory.resolve(strict=True)
    if not input_root.is_relative_to(root):
        raise CasePackageError("input/ resolves outside the case package")
    lexical_candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    # Do not merely check the resolved destination. A link from one input file
    # to another input file is still an unaudited substitution of package data.
    # Junctions that do not report as symlinks remain covered by resolve+root
    # containment below.
    component = root
    for part in PurePosixPath(relative_path).parts:
        component = component / part
        if component.is_symlink():
            raise CasePackageError(
                f"declared input path may not contain symlinks: {relative_path}"
            )
    candidate = lexical_candidate.resolve(strict=True)
    if (
        not candidate.is_file()
        or not candidate.is_relative_to(root)
        or not candidate.is_relative_to(input_root)
    ):
        raise CasePackageError(
            f"declared input file must resolve to a regular file inside input/: {relative_path}"
        )
    return candidate


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CasePackageError(f"input file is not UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CasePackageError(f"invalid JSON in {path}: {exc.msg}") from exc


def _read_jsonl(path: Path) -> list[Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CasePackageError(f"input file is not UTF-8: {path}") from exc
    rows: list[Any] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise CasePackageError(f"blank line in JSONL input {path} at line {line_number}")
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CasePackageError(
                f"invalid JSONL in {path} at line {line_number}: {exc.msg}"
            ) from exc
    return rows


def _validate_file_model(model_type: type[_StrictModel], payload: Any, label: str) -> Any:
    try:
        # Strict pydantic accepts ISO dates only through its JSON wire parser;
        # reserialising preserves JSON scalar types while rejecting coercions.
        return model_type.model_validate_json(json.dumps(payload, ensure_ascii=False))
    except ValidationError as exc:
        raise CaseValidationError(f"invalid {label}: {exc}") from exc


def _validate_model_list(model_type: type[_StrictModel], payload: Any, label: str) -> tuple[Any, ...]:
    if not isinstance(payload, list):
        raise CaseValidationError(f"{label} must be a JSON array")
    try:
        return tuple(
            model_type.model_validate_json(json.dumps(item, ensure_ascii=False))
            for item in payload
        )
    except ValidationError as exc:
        raise CaseValidationError(f"invalid {label}: {exc}") from exc


def _validate_material_sha256(path: Path, material: MaterialDescriptor) -> None:
    if material.sha256 is None:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != material.sha256:
        raise CaseValidationError(
            f"material {material.material_id} sha256 does not match {material.relative_path}"
        )


def load_due_diligence_case(case_root: str | Path) -> DueDiligenceCase:
    """Load canonical v1 input only; this module never reads or imports oracle."""

    root = _package_root(case_root)
    manifest = _validate_file_model(
        DueDiligenceManifest, _read_json(_resolve_root_manifest(root)), "manifest.json"
    )
    input_files = manifest.input
    application = _validate_file_model(
        LoanApplication,
        _read_json(_resolve_declared_input(root, input_files.application)),
        input_files.application,
    )
    entity_bundle = _validate_file_model(
        EntityBundle,
        _read_json(_resolve_declared_input(root, input_files.entities)),
        input_files.entities,
    )
    claims = _validate_model_list(
        EnterpriseClaim,
        _read_jsonl(_resolve_declared_input(root, input_files.claims)),
        input_files.claims,
    )
    source_bundle = _validate_file_model(
        SourceBundle,
        _read_json(_resolve_declared_input(root, input_files.sources)),
        input_files.sources,
    )
    materials = _validate_model_list(
        MaterialDescriptor,
        _read_json(_resolve_declared_input(root, input_files.materials)),
        input_files.materials,
    )
    for material in materials:
        material_path = _resolve_declared_input(root, material.relative_path)
        _validate_material_sha256(material_path, material)
    try:
        return DueDiligenceCase(
            manifest=manifest,
            loan_application=application,
            entities=entity_bundle.entities,
            relationships=entity_bundle.relationships,
            claims=claims,
            sources=source_bundle.sources,
            query_results=source_bundle.query_results,
            materials=materials,
        )
    except ValidationError as exc:
        raise CaseValidationError(f"invalid assembled case {manifest.case_id}: {exc}") from exc


__all__ = [
    "CaseEntity",
    "CasePackageError",
    "CaseRole",
    "CaseValidationError",
    "DueDiligenceCase",
    "DueDiligenceManifest",
    "EnterpriseClaim",
    "EntityBundle",
    "EntityKind",
    "EntityRelationship",
    "InputFileSet",
    "LoanApplication",
    "MaterialDescriptor",
    "Measurement",
    "MeasurementUnit",
    "ReportingPeriod",
    "RelationshipType",
    "SCHEMA_VERSION",
    "SourceBundle",
    "SourceChannel",
    "SourceDescriptor",
    "SourceOutcome",
    "SourceQueryResult",
    "is_valid_unified_credit_code",
    "load_due_diligence_case",
]
