"""Periodic read-only pings so managed Postgres stays active."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.core.config import Settings

logger = logging.getLogger(__name__)


async def _run_queries(settings: Settings) -> None:
    from app.db.session import get_engine

    engine = get_engine()
    async with engine.connect() as conn:
        for index, sql in enumerate(settings.db_keepalive_queries):
            try:
                await conn.execute(text(sql))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "keepalive query skipped",
                    extra={"index": index, "error": str(exc)[:200]},
                )
    logger.debug("keepalive finished", extra={"queries": len(settings.db_keepalive_queries)})


def setup_db_keepalive(settings: Settings) -> AsyncIOScheduler | None:
    if not settings.db_keepalive_enabled:
        return None
    if not settings.database_url:
        logger.warning("DB_KEEPALIVE_ENABLED but DATABASE_URL is empty")
        return None

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_queries,
        "interval",
        minutes=settings.db_keepalive_interval_minutes,
        kwargs={"settings": settings},
        id="db_keepalive",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "db keepalive on",
        extra={"interval_minutes": settings.db_keepalive_interval_minutes},
    )
    return scheduler


def shutdown_db_keepalive(scheduler: AsyncIOScheduler | None) -> None:
    if scheduler is not None:
        scheduler.shutdown(wait=False)
