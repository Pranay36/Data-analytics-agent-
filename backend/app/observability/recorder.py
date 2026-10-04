"""Persist one row per LLM attempt.

Best-effort by design: telemetry must never be the reason an analysis fails.
Every write is wrapped, and a failure is logged and swallowed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

from pydantic import BaseModel

from app.llm.types import Usage

logger = logging.getLogger(__name__)


class CallRecord(BaseModel):
    analysis_id: str | None = None
    user_id: str | None = None
    agent: str
    provider: str
    model: str
    strategy: str | None = None
    attempt: int = 1
    fallback_index: int = 0
    usage: Usage = Usage()
    latency_ms: int | None = None
    success: bool
    structured_output_valid: bool | None = None
    cached: bool = False
    error_type: str | None = None
    error_message: str | None = None


class LlmCallRecorder(Protocol):
    async def record(self, record: CallRecord) -> None: ...


class DbLlmCallRecorder:
    async def record(self, record: CallRecord) -> None:
        try:
            from app.db.models import LlmCall
            from app.db.session import get_sessionmaker

            async with get_sessionmaker()() as session:
                session.add(
                    LlmCall(
                        analysis_id=uuid.UUID(record.analysis_id) if record.analysis_id else None,
                        user_id=uuid.UUID(record.user_id) if record.user_id else None,
                        agent=record.agent,
                        provider=record.provider,
                        model=record.model,
                        attempt=record.attempt,
                        fallback_index=record.fallback_index,
                        input_tokens=record.usage.input_tokens,
                        output_tokens=record.usage.output_tokens,
                        total_tokens=record.usage.total,
                        tokens_estimated=record.usage.estimated,
                        cached=record.cached,
                        latency_ms=record.latency_ms,
                        success=record.success,
                        structured_output_valid=record.structured_output_valid,
                        error_type=record.error_type,
                        error_message=(record.error_message or "")[:500] or None,
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - telemetry must not break the run
            logger.warning("could not record llm call", extra={"error": str(exc)})


class NullRecorder:
    async def record(self, record: CallRecord) -> None:
        return None
