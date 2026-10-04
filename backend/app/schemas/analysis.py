"""API shapes for analyses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalysisCreate(BaseModel):
    datasource_id: uuid.UUID
    question: str = Field(min_length=3, max_length=1000)


class AnalysisStarted(BaseModel):
    id: uuid.UUID
    status: str


class AnalysisSummary(BaseModel):
    """One row of the history list."""

    id: uuid.UUID
    question: str
    datasource_id: uuid.UUID | None
    datasource_name: str | None
    status: str
    stage: str | None
    stop_reason: str | None
    question_type: str | None
    created_at: datetime
    latency_ms: int | None
    llm_calls: int
    tokens: int


class StepOut(BaseModel):
    """One query the pipeline ran, including the ones that were rejected or failed."""

    seq: int
    purpose: str
    step_question: str | None
    attempt: int
    status: str
    error: str | None
    sql: str | None
    original_sql: str
    tables_used: list[str]
    row_count: int | None
    execution_ms: int | None
    filters: list[Any] | None


class StatsOut(BaseModel):
    llm_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int | None
    drilldown_depth: int


class ErrorOut(BaseModel):
    code: str
    message: str


class AnalysisOut(AnalysisSummary):
    """Everything about one run: progress, the steps taken, and the dashboard."""

    steps: list[StepOut] = Field(default_factory=list)
    findings: dict[str, Any] | None = None
    retrieved_context: dict[str, Any] | None = None
    dashboard: dict[str, Any] | None = None
    """`{spec, kpis, datasets}`: the spec plus the data each widget refers to."""
    stats: StatsOut
    error: ErrorOut | None = None
