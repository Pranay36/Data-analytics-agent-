"""What an account has used today, and whether it may start another analysis.

Counted straight from `llm_calls` and `analyses` rather than kept in a rollup table: an
indexed `(user_id, created_at)` range is instant at this scale, and `today()` is the one
seam to change if that ever stops being true.

Two details that are easy to get wrong:

- Cached calls are excluded. A cache hit carries the *original* call's token counts, so
  counting it would charge someone twice for one request, and it spent no quota anyway.
- Failed calls are included. A rate-limited or invalid reply still consumed a provider
  request, and that request is what the free tier meters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Analysis, LlmCall, User


@dataclass(frozen=True)
class Usage:
    analyses: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Limits:
    analyses_per_day: int
    llm_calls_per_day: int
    tokens_per_day: int


def limits() -> Limits:
    settings = get_settings()
    return Limits(
        analyses_per_day=settings.quota_analyses_per_day,
        llm_calls_per_day=settings.quota_llm_calls_per_day,
        tokens_per_day=settings.quota_tokens_per_day,
    )


def day_start(now: datetime | None = None) -> datetime:
    """Midnight UTC. The window every quota is measured over."""
    now = now or datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def next_reset(now: datetime | None = None) -> datetime:
    return day_start(now) + timedelta(days=1)


async def today(session: AsyncSession, user_id: uuid.UUID) -> Usage:
    since = day_start()
    calls = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmCall.input_tokens), 0),
                func.coalesce(func.sum(LlmCall.output_tokens), 0),
            ).where(
                LlmCall.user_id == user_id,
                LlmCall.created_at >= since,
                LlmCall.cached.is_(False),
            )
        )
    ).one()
    analyses = await session.scalar(
        select(func.count()).where(Analysis.user_id == user_id, Analysis.created_at >= since)
    )
    return Usage(
        analyses=int(analyses or 0),
        llm_calls=int(calls[0]),
        input_tokens=int(calls[1]),
        output_tokens=int(calls[2]),
    )


async def over_quota(session: AsyncSession, user: User) -> str | None:
    """Name the first ceiling the account has reached, or None if it may proceed."""
    used, cap = await today(session, user.id), limits()
    if used.analyses >= cap.analyses_per_day:
        return "analysis"
    if used.llm_calls >= cap.llm_calls_per_day:
        return "model-call"
    if used.tokens >= cap.tokens_per_day:
        return "token"
    return None
