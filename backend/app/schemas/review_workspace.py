"""Explicit public contracts for the human-review workspace.

These models are intentionally narrow.  A review packet is a different trust
boundary from a generic research checkpoint: it must never grow automatically
when an internal graph-state key is added.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewProfileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int
    content_sha256: str
    source: str


class ReviewRiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Optional[str] = None
    composite_score: Optional[float] = None
    credit_advice: Optional[str] = None
    credit_recommendation: Optional["ReviewCreditRecommendation"] = None
    gates_applied: List[str] = Field(default_factory=list)
    triggered_rules: List[str] = Field(default_factory=list)


class ReviewCreditRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendable: Optional[bool] = None
    suggested_amount: Optional[float] = None
    currency: Optional[str] = None
    based_on_level: Optional[str] = None
    advice_text: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)


class ReviewCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_total: Optional[int] = None
    required_verified: Optional[int] = None
    verified_rate: Optional[float] = None
    unverified_fields: List[str] = Field(default_factory=list)
    conflicting_fields: List[str] = Field(default_factory=list)


class ReviewCriticalIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None


class ReviewEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    field_id: Optional[str] = None
    source_adapter: Optional[str] = None
    retrieved_at: Optional[str] = None
    as_of_date: Optional[str] = None
    active: Optional[bool] = None


class ReviewTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    company_name: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    profile_ref: Optional[ReviewProfileRef] = None
    risk_assessment: ReviewRiskAssessment
    completeness: ReviewCompleteness


class ReviewTaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[ReviewTaskSummary]
    total: int


class ReviewTaskPacket(ReviewTaskSummary):
    model_config = ConfigDict(extra="forbid")

    final_report: str
    critical_issues: List[ReviewCriticalIssue] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    evidence: List[ReviewEvidenceSummary] = Field(default_factory=list)
