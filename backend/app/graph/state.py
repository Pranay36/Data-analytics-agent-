"""What travels between graph nodes.

State holds only what a *later node needs to decide or act*. Anything that is only
for display or audit goes straight to the database instead, so the state stays
small and a run can be understood by reading it.

Deliberately absent: connectors, decrypted credentials and database sessions.
State is logged and may be persisted, and none of those belong in a log.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from app.agents.schemas import (
    AnalysisAgentOutput,
    AnalysisFrame,
    DrillDownRequest,
    QueryAgentOutput,
)
from app.dashboard import DashboardSpec
from app.graph.drilldown import DimensionInfo
from app.rag import RetrievedContext
from app.sql_guard import ValidationResult

StopReason = Literal[
    "answered",
    "max_depth",
    "inconclusive",
    "cannot_answer",
    "invalid_sql",
    "query_failed",
    "llm_unavailable",
    "budget_exhausted",
    "no_context",
    "error",
]


class DatasourceContext(BaseModel):
    """Facts about the data source that prompts and the guard need."""

    id: str
    name: str
    type: str
    dialect: str
    as_of_date: date
    currency: str | None = None


class ExecutedQuery(BaseModel):
    """One attempt at a query, successful or not.

    Failed and rejected attempts are kept: the repair step needs the error, and
    the audit trail should show what the model tried.
    """

    seq: int
    attempt: int
    purpose: Literal["primary", "drilldown"] = "primary"
    step_question: str
    original_sql: str
    sql: str | None = None
    status: Literal["rejected", "failed", "succeeded"]
    error: str | None = None
    tables_used: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_ms: int | None = None
    explanation: str = ""
    filters: list[dict[str, str]] = Field(default_factory=list)
    """Segments this query was restricted to, accumulated down the drill-down path."""
    dimension: str | None = None
    profile: dict | None = None
    """Deterministic statistics, as JSON. See `app.analytics.profiler`."""


class RunError(BaseModel):
    code: str
    message: str


class AnalysisState(TypedDict, total=False):
    # ── Fixed for the run ────────────────────────────────────────────────────
    analysis_id: str
    datasource_id: str
    user_question: str

    # ── Loaded once ──────────────────────────────────────────────────────────
    datasource: DatasourceContext
    allowed_tables: list[str]
    """Qualified names the SQL guard will accept. Excludes anything marked
    non-queryable, so a table hidden from the model is also blocked if guessed."""
    known_columns: dict[str, list[str]]
    dimensions: list[DimensionInfo]
    """Low-cardinality columns the Analysis Agent may break a metric down by."""

    # ── The current step; overwritten each time round the loop ───────────────
    mode: Literal["primary", "drilldown"]
    current_question: str
    retrieved: RetrievedContext
    pending: QueryAgentOutput
    attempt: int
    """1 for the first try at a step, rising with each repair."""
    last_error: str | None
    validation: ValidationResult

    # ── Accumulated; nodes append, never replace ─────────────────────────────
    queries: Annotated[list[ExecutedQuery], operator.add]

    # ── Drill-down: what has been investigated, so the loop cannot repeat itself ─
    drilldown_depth: int
    filter_path: list[dict[str, str]]
    used_dimensions: list[str]
    drill: DrillDownRequest | None
    """The approved request the next Query Agent call must carry out."""
    analysis_rounds: Annotated[list[AnalysisAgentOutput], operator.add]
    notes: Annotated[list[str], operator.add]
    """Caveats to show with the final answer, such as why an investigation stopped."""

    # ── Established by the first query, then held fixed ──────────────────────
    question_type: str | None
    frame: AnalysisFrame | None

    # ── Dashboard ────────────────────────────────────────────────────────────
    dashboard_draft: DashboardSpec | None
    """What the Visualization Agent proposed, before validation."""
    dashboard: DashboardSpec | None
    dashboard_source: Literal["llm", "fallback"] | None
    dashboard_dropped: list[str]
    """Why widgets were dropped or changed. Kept for diagnosis, not shown to users."""

    # ── Outcome ──────────────────────────────────────────────────────────────
    stop_reason: StopReason | None
    error: RunError | None
