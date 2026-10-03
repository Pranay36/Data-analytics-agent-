"""Writing run progress to the application database.

Best-effort throughout. Progress and audit rows exist to make a run observable; if
writing one fails, the analysis itself must carry on rather than die for want of a
log entry.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from app.db.models import Analysis, AnalysisQuery
from app.graph.deps import GraphDeps
from app.graph.state import ExecutedQuery

logger = logging.getLogger(__name__)


def _id(analysis_id: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(analysis_id) if analysis_id else None
    except ValueError:
        return None


async def set_stage(deps: GraphDeps, analysis_id: str | None, stage: str) -> None:
    deps.stages.append(stage)
    if (key := _id(analysis_id)) is None:
        return
    try:
        async with deps.sessionmaker() as session:
            await session.execute(
                update(Analysis).where(Analysis.id == key).values(status="running", stage=stage)
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - progress is advisory
        logger.warning("could not record stage", extra={"stage": stage, "error": str(exc)})


async def save_query(deps: GraphDeps, analysis_id: str | None, query: ExecutedQuery) -> None:
    if (key := _id(analysis_id)) is None:
        return
    try:
        async with deps.sessionmaker() as session:
            session.add(
                AnalysisQuery(
                    analysis_id=key,
                    seq=query.seq,
                    purpose=query.purpose,
                    step_question=query.step_question,
                    attempt=query.attempt,
                    original_sql=query.original_sql,
                    sql=query.sql,
                    status=query.status,
                    error=query.error,
                    tables_used=query.tables_used or None,
                    row_count=query.row_count,
                    truncated=query.truncated,
                    execution_ms=query.execution_ms,
                    result_columns=query.columns or None,
                    result_preview=query.rows or None,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record query", extra={"seq": query.seq, "error": str(exc)})


async def finish_analysis(
    deps: GraphDeps, analysis_id: str | None, values: dict[str, Any]
) -> None:
    if (key := _id(analysis_id)) is None:
        return
    try:
        async with deps.sessionmaker() as session:
            await session.execute(
                update(Analysis)
                .where(Analysis.id == key)
                .values(**values, completed_at=datetime.now(UTC))
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not finalise analysis", extra={"error": str(exc)})
