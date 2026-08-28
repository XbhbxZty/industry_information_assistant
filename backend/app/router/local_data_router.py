# Copyright © 2026 XbhbxZty
"""One read-only API over all normalized public due-diligence datasets."""

from __future__ import annotations

import hmac
import os
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from service.local_data_repository import LocalDataNotFound, LocalDataRepository


def require_local_data_key(x_local_data_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("LOCAL_DATA_API_KEY")
    if expected and not hmac.compare_digest(x_local_data_key or "", expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid local data key")


@lru_cache(maxsize=1)
def get_local_data_repository() -> LocalDataRepository:
    return LocalDataRepository()


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    domains: list[str] = Field(default_factory=list)
    top_k: int = Field(default=20, ge=1, le=100)


router = APIRouter(prefix="/local-data", tags=["本地公开尽调数据"], dependencies=[Depends(require_local_data_key)])


def _not_found(exc: LocalDataNotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/health")
async def health(repository: LocalDataRepository = Depends(get_local_data_repository)):
    snapshot = repository.snapshot()
    return {"status": snapshot["status"], "snapshot": snapshot}


@router.get("/snapshots/current")
async def current_snapshot(repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.snapshot()


@router.get("/datasets")
async def datasets(repository: LocalDataRepository = Depends(get_local_data_repository)):
    rows = repository.datasets()
    return {"items": rows, "total": len(rows)}


@router.get("/companies")
async def companies(
    query: Optional[str] = None, dataset_id: Optional[str] = None, market: Optional[str] = None,
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    repository: LocalDataRepository = Depends(get_local_data_repository),
):
    return repository.companies(query=query, dataset_id=dataset_id, market=market, offset=offset, limit=limit)


@router.get("/companies/resolve")
async def resolve_company(query: str = Query(..., min_length=1, max_length=200), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.resolve_company(query)


@router.get("/companies/{company_id}")
async def company(company_id: str, repository: LocalDataRepository = Depends(get_local_data_repository)):
    try:
        return repository.company(company_id)
    except LocalDataNotFound as exc:
        raise _not_found(exc) from exc


@router.get("/documents")
async def documents(
    company_id: Optional[str] = None, dataset_id: Optional[str] = None,
    document_type: Optional[str] = None, local_only: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    repository: LocalDataRepository = Depends(get_local_data_repository),
):
    return repository.documents(company_id=company_id, dataset_id=dataset_id, document_type=document_type, local_only=local_only, offset=offset, limit=limit)


@router.get("/companies/{company_id}/documents")
async def company_documents(company_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.documents(company_id=company_id, offset=offset, limit=limit)


@router.get("/financial-samples")
async def financial_samples(company_id: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.financial_samples(company_id=company_id, offset=offset, limit=limit)


@router.get("/tasks")
async def tasks(company_id: Optional[str] = None, task_type: Optional[str] = None, query: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.tasks(company_id=company_id, task_type=task_type, query=query, offset=offset, limit=limit)


@router.get("/legal-cases")
async def legal_cases(query: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.legal_cases(query=query, offset=offset, limit=limit)


@router.get("/research-structures")
async def research_structures(company_id: Optional[str] = None, query: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), repository: LocalDataRepository = Depends(get_local_data_repository)):
    return repository.research_structures(company_id=company_id, query=query, offset=offset, limit=limit)


@router.post("/search")
async def search(request: SearchRequest, repository: LocalDataRepository = Depends(get_local_data_repository)):
    rows = repository.search(request.query, domains=request.domains or None, top_k=request.top_k)
    return {"items": rows, "total": len(rows)}


@router.get("/documents/{document_id}/file")
async def document_file(document_id: str, repository: LocalDataRepository = Depends(get_local_data_repository)):
    try:
        path, media_type = repository.document_path(document_id)
    except LocalDataNotFound as exc:
        raise _not_found(exc) from exc
    return FileResponse(path, media_type=media_type, filename=path.name)
