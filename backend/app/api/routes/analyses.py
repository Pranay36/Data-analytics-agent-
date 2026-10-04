"""Analysis endpoints. HTTP concerns only; behaviour lives in the service."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.analysis import AnalysisCreate, AnalysisOut, AnalysisStarted, AnalysisSummary
from app.services import analysis_service as service

router = APIRouter(prefix="/analyses", tags=["analyses"])


@router.post("", response_model=AnalysisStarted, status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(
    body: AnalysisCreate, session: AsyncSession = Depends(get_session)
) -> AnalysisStarted:
    """Start an analysis. Returns at once; poll `GET /analyses/{id}` for progress."""
    try:
        analysis = await service.start_analysis(session, body.datasource_id, body.question)
    except service.DataSourceMissing as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.") from exc
    return AnalysisStarted(id=analysis.id, status=analysis.status)


@router.get("", response_model=list[AnalysisSummary])
async def list_analyses(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    datasource_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisSummary]:
    return await service.list_analyses(
        session, limit=limit, offset=offset, datasource_id=datasource_id
    )


@router.get("/{analysis_id}", response_model=AnalysisOut)
async def get_analysis(
    analysis_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AnalysisOut:
    """Progress while running; the full result, with its dashboard, once complete."""
    try:
        return await service.get_analysis(session, analysis_id)
    except service.AnalysisMissing as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.") from exc
