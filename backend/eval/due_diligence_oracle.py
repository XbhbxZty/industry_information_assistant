"""Evaluator-only loader for canonical factoring-case oracle answers.

Production code must not import this module.  The production loader has no
reference to ``eval`` or ``oracle``; this module instead receives a completed
production :class:`DueDiligenceCase` and validates hidden answers against it.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from service.due_diligence_case import (
    DueDiligenceCase,
    Measurement,
    MeasurementUnit,
    SCHEMA_VERSION,
)


class OracleLoadError(ValueError):
    """A hidden evaluator oracle cannot safely be loaded or matched to a case."""


class ExpectedDecision(str, Enum):
    APPROVE = "approve"
    CONDITIONAL_APPROVE = "conditional_approve"
    REJECT = "reject"
    DEFER_MANUAL_REVIEW = "defer_manual_review"


class ExpectedClaimVerdict(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


class _OracleModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


def _require_id(value: str, field_name: str) -> str:
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(f"{field_name} must be a portable case identifier")
    return value


class OracleClaimAssessment(_OracleModel):
    claim_id: str
    verdict: ExpectedClaimVerdict
    source_refs: tuple[str, ...] = ()
    rationale: str = Field(min_length=1, max_length=4096)

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _require_id(value, "claim_id")

    @field_validator("source_refs")
    @classmethod
    def _validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(_require_id(item, "source_refs item") for item in value)
        if len(validated) != len(set(validated)):
            raise ValueError("source_refs must not contain duplicates")
        return validated


class OracleConflict(_OracleModel):
    """A known inconsistency that cannot be reduced to a single claim verdict."""

    conflict_id: str
    claim_id: str
    assertion_source: str
    contrary_source_ids: tuple[str, ...] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=4096)
    rationale: str = Field(min_length=1, max_length=4096)

    @field_validator("conflict_id", "claim_id", "assertion_source")
    @classmethod
    def _validate_ids(cls, value: str, info: Any) -> str:
        return _require_id(value, info.field_name)

    @field_validator("contrary_source_ids")
    @classmethod
    def _validate_contrary_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(_require_id(item, "contrary_source_ids item") for item in value)
        if len(validated) != len(set(validated)):
            raise ValueError("contrary_source_ids must not contain duplicates")
        return validated


class DueDiligenceOracle(_OracleModel):
    schema_version: Literal[SCHEMA_VERSION]
    case_id: str
    expected_decision: ExpectedDecision
    expected_claim_verdicts: tuple[OracleClaimAssessment, ...]
    known_conflicts: tuple[OracleConflict, ...] = ()
    conditions: tuple[str, ...] = ()
    maximum_advance: Measurement | None = None
    rationale: str = Field(min_length=1, max_length=8192)

    @field_validator("case_id")
    @classmethod
    def _validate_case_id(cls, value: str) -> str:
        return _require_id(value, "case_id")

    @model_validator(mode="after")
    def _validate_unique_claim_assessments(self) -> "DueDiligenceOracle":
        claim_ids = [item.claim_id for item in self.expected_claim_verdicts]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("duplicate oracle claim assessments are not allowed")
        conflict_ids = [item.conflict_id for item in self.known_conflicts]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("duplicate oracle conflict ids are not allowed")
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("oracle conditions must not contain duplicates")
        if any(not condition for condition in self.conditions):
            raise ValueError("oracle conditions must not be blank")
        if self.expected_decision is ExpectedDecision.CONDITIONAL_APPROVE:
            if not self.conditions:
                raise ValueError("conditional_approve requires at least one condition")
        elif self.conditions or self.maximum_advance is not None:
            raise ValueError(
                "only conditional_approve may declare conditions or maximum_advance"
            )
        if self.maximum_advance is not None and (
            self.maximum_advance.unit
            not in {
                MeasurementUnit.CNY,
                MeasurementUnit.CNY_TEN_THOUSAND,
                MeasurementUnit.CNY_MILLION,
            }
            or self.maximum_advance.value <= 0
        ):
            raise ValueError("maximum_advance must be a positive CNY-denominated measurement")
        return self

    def validate_against_case(self, case: DueDiligenceCase) -> None:
        """Confirm this hidden answer belongs exactly to the supplied input case."""

        if self.schema_version != case.manifest.schema_version:
            raise OracleLoadError("oracle schema_version does not match production case")
        if self.case_id != case.manifest.case_id:
            raise OracleLoadError("oracle case_id does not match production case")
        case_claim_ids = {claim.claim_id for claim in case.claims}
        oracle_claim_ids = {item.claim_id for item in self.expected_claim_verdicts}
        if oracle_claim_ids != case_claim_ids:
            raise OracleLoadError(
                "oracle claim ids must exactly match production claims "
                f"(missing={sorted(case_claim_ids - oracle_claim_ids)}, "
                f"unknown={sorted(oracle_claim_ids - case_claim_ids)})"
            )
        case_source_ids = {source.source_id for source in case.sources}
        for assessment in self.expected_claim_verdicts:
            unknown_refs = set(assessment.source_refs) - case_source_ids
            if unknown_refs:
                raise OracleLoadError(
                    f"oracle claim {assessment.claim_id} references unknown sources: "
                    f"{sorted(unknown_refs)}"
                )
        conflict_ids = set()
        for conflict in self.known_conflicts:
            if conflict.conflict_id in conflict_ids:
                # Defensive even though the model validator rejects this.
                raise OracleLoadError(f"duplicate oracle conflict id: {conflict.conflict_id}")
            conflict_ids.add(conflict.conflict_id)
            if conflict.claim_id not in case_claim_ids:
                raise OracleLoadError(
                    f"oracle conflict {conflict.conflict_id} references unknown claim: "
                    f"{conflict.claim_id}"
                )
            source_refs = {conflict.assertion_source, *conflict.contrary_source_ids}
            unknown_refs = source_refs - case_source_ids
            if unknown_refs:
                raise OracleLoadError(
                    f"oracle conflict {conflict.conflict_id} references unknown sources: "
                    f"{sorted(unknown_refs)}"
                )


def _resolve_oracle_path(case_root: str | Path) -> Path:
    root = Path(case_root).resolve(strict=True)
    if not root.is_dir():
        raise OracleLoadError(f"case root is not a directory: {root}")
    oracle_directory = root / "oracle"
    if oracle_directory.is_symlink() or not oracle_directory.is_dir():
        raise OracleLoadError("oracle/ must be a real directory inside the case package")
    oracle_root = oracle_directory.resolve(strict=True)
    if not oracle_root.is_relative_to(root):
        raise OracleLoadError("oracle/ resolves outside the case package")
    expected = oracle_directory / "expected.json"
    if expected.is_symlink() or not expected.is_file():
        raise OracleLoadError("oracle/expected.json must be a regular file")
    resolved = expected.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_relative_to(oracle_root):
        raise OracleLoadError("oracle/expected.json resolves outside oracle/")
    return resolved


def load_due_diligence_oracle(
    case_root: str | Path, production_case: DueDiligenceCase
) -> DueDiligenceOracle:
    """Load fixed ``oracle/expected.json`` and match it to an explicit case."""

    if not isinstance(production_case, DueDiligenceCase):
        raise TypeError("production_case must be a loaded DueDiligenceCase")
    path = _resolve_oracle_path(case_root)
    try:
        oracle = DueDiligenceOracle.model_validate_json(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise OracleLoadError(f"oracle file is not UTF-8: {path}") from exc
    except ValidationError as exc:
        raise OracleLoadError(f"invalid oracle expected.json: {exc}") from exc
    oracle.validate_against_case(production_case)
    return oracle


__all__ = [
    "DueDiligenceOracle",
    "ExpectedClaimVerdict",
    "ExpectedDecision",
    "OracleClaimAssessment",
    "OracleConflict",
    "OracleLoadError",
    "load_due_diligence_oracle",
]
