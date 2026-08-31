"""Administrator company-profile CRUD and isolated material retrieval API."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.database import get_db
from models.user import User
from router.auth_router import get_current_user_required, require_superuser
from schemas.company_profile import (
    CompanyProfileArchive, CompanyProfileAuditResponse, CompanyProfileCreate,
    CompanyProfileHistoryResponse, CompanyProfileListResponse, CompanyProfileResponse,
    CompanyProfileSummary, CompanyProfileUpdate, MaterialSearchRequest,
    MaterialSearchResponse,
)
from service.admin_company_profile_service import (
    AdminCompanyProfileConflict, AdminCompanyProfileIntegrityError, AdminCompanyProfileNotFound,
    AdminCompanyProfileValidationError, archive_company_profile, company_profile_templates,
    create_company_profile, get_company_profile, list_company_profile_history,
    list_company_profiles, search_profile_materials, update_company_profile,
)


router = APIRouter(prefix="/company-profiles", tags=["管理员企业档案"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, AdminCompanyProfileNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AdminCompanyProfileConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, AdminCompanyProfileIntegrityError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, AdminCompanyProfileValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="企业档案服务异常")


def _summary(row, *, reveal_actor_ids: bool) -> CompanyProfileSummary:
    values = dict(
        id=str(row.id), name=row.name, credit_code=row.credit_code, status=row.status,
        revision=row.revision, content_sha256=row.content_sha256, scenario=row.scenario or "",
        updated_at=row.updated_at,
    )
    if reveal_actor_ids:
        values["updated_by"] = row.updated_by
    return CompanyProfileSummary(**values)


def _detail(row, *, reveal_actor_ids: bool) -> CompanyProfileResponse:
    values = dict(
        **_summary(
            row, reveal_actor_ids=reveal_actor_ids,
        ).model_dump(exclude_unset=True),
        profile=row.profile_json or {},
        scenario_data=row.scenario_data or {}, field_sources=row.field_sources or [],
        materials=row.materials or [], created_at=row.created_at, archived_at=row.archived_at,
    )
    if reveal_actor_ids:
        values["created_by"] = row.created_by
        values["archived_by"] = row.archived_by
    return CompanyProfileResponse(**values)


@router.get("/templates")
async def get_templates(current_user: User = Depends(get_current_user_required)):
    """Canonical write template; any authenticated user may read it."""
    return company_profile_templates()


@router.get(
    "", response_model=CompanyProfileListResponse,
    response_model_exclude_unset=True,
)
async def get_profiles(
    query: str = Query("", max_length=255), include_archived: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user_required), db: Session = Depends(get_db),
):
    # Archive visibility is a server-side privilege decision, never a query flag.
    rows, total = list_company_profiles(
        db, query=query, include_archived=bool(include_archived and current_user.is_superuser),
        offset=offset, limit=limit,
    )
    return CompanyProfileListResponse(
        items=[
            _summary(row, reveal_actor_ids=current_user.is_superuser)
            for row in rows
        ],
        total=total, offset=offset, limit=limit,
    )


@router.post("", response_model=CompanyProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: CompanyProfileCreate, current_user: User = Depends(require_superuser),
    db: Session = Depends(get_db),
):
    try:
        row = create_company_profile(db, payload, actor_id=str(current_user.id),
                                     change_reason=payload.change_reason)
        return _detail(row, reveal_actor_ids=True)
    except (AdminCompanyProfileConflict, AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc


@router.get(
    "/{profile_id}", response_model=CompanyProfileResponse,
    response_model_exclude_unset=True,
)
async def get_profile(
    profile_id: str, current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    try:
        return _detail(
            get_company_profile(
                db, profile_id, include_archived=current_user.is_superuser,
            ),
            reveal_actor_ids=current_user.is_superuser,
        )
    except (AdminCompanyProfileNotFound, AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc


@router.put("/{profile_id}", response_model=CompanyProfileResponse)
async def replace_profile(
    profile_id: str, payload: CompanyProfileUpdate,
    current_user: User = Depends(require_superuser), db: Session = Depends(get_db),
):
    try:
        row = update_company_profile(
            db, profile_id, payload, expected_revision=payload.expected_revision,
            actor_id=str(current_user.id), change_reason=payload.change_reason,
        )
        return _detail(row, reveal_actor_ids=True)
    except (AdminCompanyProfileNotFound, AdminCompanyProfileConflict,
            AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc


@router.post("/{profile_id}/archive", response_model=CompanyProfileResponse)
async def archive_profile(
    profile_id: str, payload: CompanyProfileArchive,
    current_user: User = Depends(require_superuser), db: Session = Depends(get_db),
):
    try:
        row = archive_company_profile(
            db, profile_id, expected_revision=payload.expected_revision,
            actor_id=str(current_user.id), change_reason=payload.change_reason,
        )
        return _detail(row, reveal_actor_ids=True)
    except (AdminCompanyProfileNotFound, AdminCompanyProfileConflict,
            AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc


@router.get("/{profile_id}/history", response_model=CompanyProfileHistoryResponse)
async def profile_history(
    profile_id: str, current_user: User = Depends(require_superuser), db: Session = Depends(get_db),
):
    try:
        rows = list_company_profile_history(db, profile_id)
        return CompanyProfileHistoryResponse(
            items=[CompanyProfileAuditResponse(
                id=str(row.id), profile_id=row.profile_id, revision=row.revision, action=row.action,
                actor_id=row.actor_id, change_reason=row.change_reason,
                before_snapshot=row.before_snapshot, after_snapshot=row.after_snapshot,
                content_sha256=row.content_sha256, created_at=row.created_at,
            ) for row in rows], total=len(rows),
        )
    except (AdminCompanyProfileNotFound, AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc


@router.post("/{profile_id}/materials/search", response_model=MaterialSearchResponse)
async def search_materials(
    profile_id: str, payload: MaterialSearchRequest,
    current_user: User = Depends(get_current_user_required), db: Session = Depends(get_db),
):
    try:
        return search_profile_materials(
            db, profile_id, query=payload.query, limit=payload.limit,
            include_archived=current_user.is_superuser,
        )
    except (AdminCompanyProfileNotFound, AdminCompanyProfileValidationError) as exc:
        raise _error(exc) from exc
