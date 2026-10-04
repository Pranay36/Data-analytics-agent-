"""One analysis run, the SQL it generated, and the dashboard it produced."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk


class Analysis(Base, TimestampMixin):
    __tablename__ = "analyses"
    __table_args__ = (
        Index("ix_analyses_created", "created_at"),
        Index("ix_analyses_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    """Who asked. Every read path filters on this; it is the access boundary."""
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="queued")
    """queued | running | completed | failed"""
    stage: Mapped[str | None] = mapped_column(String(48), nullable=True)
    """Current node, shown by the progress stepper."""

    question_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    analysis_frame: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retrieved_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    findings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    drilldown_depth: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    llm_calls_count: Mapped[int] = mapped_column(Integer, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queries: Mapped[list[AnalysisQuery]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="AnalysisQuery.seq"
    )
    dashboard: Mapped[Dashboard | None] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", uselist=False
    )


class AnalysisQuery(Base):
    """Every SQL statement generated in a run, including rejected ones.

    This is both the audit log and "the SQL behind each widget": dashboard
    widgets reference these rows, which is why refresh needs no new SQL.
    """

    __tablename__ = "analysis_queries"
    __table_args__ = (UniqueConstraint("analysis_id", "seq", name="uq_analysis_query_seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(16), default="primary")
    """primary | drilldown"""
    step_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    original_sql: Mapped[str] = mapped_column(Text)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What actually ran, after the guard added its row cap."""
    status: Mapped[str] = mapped_column(String(16))
    """rejected | failed | succeeded"""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tables_used: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_columns: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    result_preview: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    """Capped rows only. A derived extract, never the customer's warehouse."""
    filters: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    analysis: Mapped[Analysis] = relationship(back_populates="queries")


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="CASCADE"),
        unique=True,
    )
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    generated_by: Mapped[str] = mapped_column(String(16), default="llm")
    """llm | fallback"""
    refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    analysis: Mapped[Analysis] = relationship(back_populates="dashboard")
