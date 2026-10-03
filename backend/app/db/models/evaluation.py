"""Stored results of an evaluation run."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, uuid_pk


class EvaluationRun(Base):
    """One execution of an evaluation suite.

    Per-case results live in a JSONB column rather than their own table: they are only
    ever read back as a whole, and a suite is a few dozen cases. Promote it to a table
    if suites grow into the hundreds or need querying across runs.
    """

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    suite: Mapped[str] = mapped_column(String(64))
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """Models, retrieval settings and limits in force, so a score can be traced to
    the configuration that produced it."""
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    results: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
