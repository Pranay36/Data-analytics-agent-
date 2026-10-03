"""Async SQLAlchemy engine and session factory for the application database.

This is the *system plane* only (PROJECT_PLAN §2.3): InsightFlow's own metadata
and vectors. Customer analytical databases are reached through
`app.connectors`, never through this module.

The engine is created lazily so that importing the app (and answering `/health`)
never requires a reachable database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().require_database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    async with get_sessionmaker()() as session:
        yield session


async def check_database() -> bool:
    """Readiness probe: can we run a trivial query?"""
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
