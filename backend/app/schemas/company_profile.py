"""Pydantic contracts for administrator-maintained company profiles."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldSourceInput(BaseModel):
    """A source eligible to support structured due-diligence fields."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    issuer: str = Field(..., min_length=1, max_length=255)
    source_type: Literal["official", "authorized", "audited"]
    field_ids: List[str] = Field(..., min_length=1, max_length=64)
    retrieved_at: str = Field(..., min_length=1, max_length=64)
    as_of_date: str = Field(..., min_length=1, max_length=64)
    reference: str = Field(..., min_length=1, max_length=1000)
    sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)


class MaterialInput(BaseModel):
    """Weak, profile-scoped retrieval material.  It is never structured evidence."""

    model_config = ConfigDict(extra="forbid")

    material_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    source_type: Literal["company_submitted", "admin_observation"]
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=12000)
    reference: Optional[str] = Field(default=None, max_length=1000)
    as_of_date: Optional[str] = Field(default=None, min_length=1, max_length=64)
    date_unknown_reason: Optional[str] = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _has_exactly_one_date_statement(self):
        if bool(self.as_of_date) == bool(self.date_unknown_reason):
            raise ValueError("materials 必须二选一提供 as_of_date 或 date_unknown_reason")
        return self


class CompanyProfileWrite(BaseModel):
    """A full replacement payload.  Client supplied coverage is ignored/rebuilt."""

    model_config = ConfigDict(extra="forbid")

    profile: Dict[str, Any] = Field(default_factory=dict)
    scenario: Literal["", "factoring"] = ""
    scenario_data: Dict[str, Any] = Field(default_factory=dict)
    field_sources: List[FieldSourceInput] = Field(default_factory=list, max_length=64)
    materials: List[MaterialInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _requires_only_name(self):
        name = self.profile.get("name") if isinstance(self.profile, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("profile.name 是创建档案的唯一必填业务字段")
        return self


class CompanyProfileCreate(CompanyProfileWrite):
    change_reason: str = Field(default="创建管理员企业档案", min_length=1, max_length=500)


class CompanyProfileUpdate(CompanyProfileWrite):
    expected_revision: int = Field(..., ge=1)
    change_reason: str = Field(..., min_length=1, max_length=500)


class CompanyProfileArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1)
    change_reason: str = Field(..., min_length=1, max_length=500)


class MaterialSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class FieldSourceResponse(FieldSourceInput):
    """Strict public projection of a persisted structured-field source."""


class MaterialResponse(MaterialInput):
    """Strict public projection of weak profile-scoped retrieval material."""

    material_id: str = Field(..., min_length=1, max_length=128)
    eligible_for_structured_evidence: Literal[False] = False


class MaterialSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[MaterialResponse]
    total: int = Field(..., ge=0)


class CompanyProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    credit_code: Optional[str] = None
    status: str
    revision: int
    content_sha256: str
    scenario: str
    updated_at: datetime
    updated_by: Optional[str] = None


class CompanyProfileResponse(CompanyProfileSummary):
    profile: Dict[str, Any]
    scenario_data: Dict[str, Any]
    field_sources: List[FieldSourceResponse]
    materials: List[MaterialResponse]
    created_at: datetime
    created_by: Optional[str] = None
    archived_at: Optional[datetime] = None
    archived_by: Optional[str] = None


class CompanyProfileListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[CompanyProfileSummary]
    total: int
    offset: int
    limit: int


class CompanyProfileAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    revision: int
    action: str
    actor_id: Optional[str] = None
    change_reason: str
    before_snapshot: Optional[Dict[str, Any]] = None
    after_snapshot: Dict[str, Any]
    content_sha256: str
    created_at: datetime


class CompanyProfileHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[CompanyProfileAuditResponse]
    total: int
