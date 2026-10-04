"""Running analyses in the background and reading them back.

A run takes from a few seconds to a minute on free-tier models, rate-limit waits
included. Holding an HTTP request open that long is fragile: proxies time out and a page
refresh loses everything. So starting a run returns immediately, the run writes its stage
to the database as it goes, and clients poll. That also means history needs no extra work:
every run is already a row.

An in-process background task is deliberate. The jobs are short, there is no requirement
that they survive a restart, and a queue would add a second service for no benefit here.
What replaces durability is `reconcile_orphans`: a run interrupted by a restart is marked
failed at startup instead of sitting at "running" forever.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.dashboard import DashboardSpec, hydrate
from app.db.models import Analysis, AnalysisQuery, DataSource
from app.db.session import get_sessionmaker
from app.schemas.analysis import (
    AnalysisOut,
    AnalysisSummary,
    ErrorOut,
    StatsOut,
    StepOut,
)
from app.services.analysis_runner import run_analysis

logger = logging.getLogger(__name__)

# Strong references: the event loop keeps only weak ones, so an unreferenced task can be
# garbage collected part-way through a run.
_running: set[asyncio.Task] = set()

# Free-tier providers rate-limit per minute, so too many analyses at once only make each
# other wait. Extra runs stay "queued" until a slot frees up.
MAX_CONCURRENT_RUNS = 2
_slots: asyncio.Semaphore | None = None

# Test seam: lets a test supply a scripted model and embeddings instead of real ones.
overrides: dict[str, Any] = {"llm": None, "embeddings": None}

TERMINAL = {"completed", "failed"}


class DataSourceMissing(LookupError):
    pass


class AnalysisMissing(LookupError):
    pass


def _semaphore() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    return _slots


async def _run(analysis_id: uuid.UUID, datasource_id: uuid.UUID, question: str) -> None:
    async with _semaphore():
        try:
            await run_analysis(
                datasource_id,
                question,
                analysis_id=analysis_id,
                llm=overrides["llm"],
                embeddings=overrides["embeddings"],
            )
        except Exception:  # noqa: BLE001 - run_analysis has already recorded the failure
            logger.exception("background analysis failed", extra={"analysis_id": str(analysis_id)})


async def start_analysis(
    session: AsyncSession, datasource_id: uuid.UUID, question: str
) -> Analysis:
    """Create the run and return at once; the work continues in the background."""
    if await session.get(DataSource, datasource_id) is None:
        raise DataSourceMissing(str(datasource_id))

    analysis = Analysis(data_source_id=datasource_id, question=question.strip(), status="queued")
    session.add(analysis)
    await session.commit()

    task = asyncio.create_task(_run(analysis.id, datasource_id, analysis.question))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return analysis


async def reconcile_orphans(session: AsyncSession | None = None) -> int:
    """Mark runs left unfinished by a restart as failed.

    Without this a run interrupted mid-flight stays "running" for ever, and a polling
    client waits on it for ever.
    """
    own = session is None
    session = session or get_sessionmaker()()
    try:
        result = await session.execute(
            update(Analysis)
            .where(Analysis.status.in_(("queued", "running")))
            .values(
                status="failed",
                stage="failed",
                stop_reason="error",
                error_code="INTERRUPTED",
                error_message="The server restarted while this analysis was running. Run it again.",
            )
        )
        await session.commit()
        return result.rowcount or 0
    finally:
        if own:
            await session.close()


# ── Reading ──────────────────────────────────────────────────────────────────
@dataclass
class StoredQuery:
    """A stored query, shaped like the executed queries the dashboard code expects."""

    seq: int
    status: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    sql: str | None
    step_question: str | None


def _stored(query: AnalysisQuery) -> StoredQuery:
    return StoredQuery(
        seq=query.seq,
        status=query.status,
        columns=list(query.result_columns or []),
        rows=list(query.result_preview or []),
        row_count=query.row_count or 0,
        truncated=query.truncated,
        sql=query.sql,
        step_question=query.step_question,
    )


def _summary(analysis: Analysis, datasource_name: str | None) -> dict[str, Any]:
    return {
        "id": analysis.id,
        "question": analysis.question,
        "datasource_id": analysis.data_source_id,
        "datasource_name": datasource_name,
        "status": analysis.status,
        "stage": analysis.stage,
        "stop_reason": analysis.stop_reason,
        "question_type": analysis.question_type,
        "created_at": analysis.created_at,
        "latency_ms": analysis.latency_ms,
        "llm_calls": analysis.llm_calls_count,
        "tokens": analysis.total_input_tokens + analysis.total_output_tokens,
    }


async def get_analysis(session: AsyncSession, analysis_id: uuid.UUID) -> AnalysisOut:
    analysis = await session.scalar(
        select(Analysis)
        .where(Analysis.id == analysis_id)
        .options(selectinload(Analysis.queries), selectinload(Analysis.dashboard))
    )
    if analysis is None:
        raise AnalysisMissing(str(analysis_id))

    source = (
        await session.get(DataSource, analysis.data_source_id) if analysis.data_source_id else None
    )

    dashboard = None
    if analysis.dashboard is not None:
        spec = DashboardSpec.model_validate(analysis.dashboard.spec)
        dashboard = hydrate(spec, [_stored(q) for q in analysis.queries])
        dashboard["generated_by"] = analysis.dashboard.generated_by

    # A refusal completes rather than fails, but the user still needs to be told why.
    error = None
    if analysis.error_message:
        code = analysis.error_code or (analysis.stop_reason or "FAILED").upper()
        error = ErrorOut(code=code, message=analysis.error_message)

    return AnalysisOut(
        **_summary(analysis, source.name if source else None),
        steps=[
            StepOut(
                seq=q.seq,
                purpose=q.purpose,
                step_question=q.step_question,
                attempt=q.attempt,
                status=q.status,
                error=q.error,
                sql=q.sql,
                original_sql=q.original_sql,
                tables_used=list(q.tables_used or []),
                row_count=q.row_count,
                execution_ms=q.execution_ms,
                filters=q.filters,
            )
            for q in analysis.queries
        ],
        findings=analysis.findings,
        retrieved_context=analysis.retrieved_context,
        dashboard=dashboard,
        stats=StatsOut(
            llm_calls=analysis.llm_calls_count,
            input_tokens=analysis.total_input_tokens,
            output_tokens=analysis.total_output_tokens,
            latency_ms=analysis.latency_ms,
            drilldown_depth=analysis.drilldown_depth,
        ),
        error=error,
    )


async def list_analyses(
    session: AsyncSession,
    *,
    limit: int = 25,
    offset: int = 0,
    datasource_id: uuid.UUID | None = None,
) -> list[AnalysisSummary]:
    query = (
        select(Analysis, DataSource.name)
        .outerjoin(DataSource, DataSource.id == Analysis.data_source_id)
        .order_by(Analysis.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if datasource_id is not None:
        query = query.where(Analysis.data_source_id == datasource_id)

    rows = (await session.execute(query)).all()
    return [AnalysisSummary(**_summary(analysis, name)) for analysis, name in rows]


def limits() -> dict[str, int]:
    settings = get_settings()
    return {"max_question_length": 1000, "max_drilldown_depth": settings.max_drilldown_depth}
