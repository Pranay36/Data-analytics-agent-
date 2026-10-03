"""What the agents are asked to produce.

Kept deliberately flat. Free-tier models handle a shallow object reliably and a
deeply nested one poorly, and every extra optional field is another way for a
reply to fail validation. Anything that can be computed deterministically is
computed in code rather than asked of the model.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

QuestionType = Literal["metric", "trend", "comparison", "breakdown", "top_n", "root_cause"]


class Period(BaseModel):
    """A half-open date range: `start` inclusive, `end` exclusive.

    Half-open because `order_date < '2026-07-01'` is correct for every timestamp
    in June, where `<= '2026-06-30'` silently drops everything after midnight.
    """

    start: date
    end: date
    label: str = Field(description="Human label, e.g. 'June 2026'.")


class AnalysisFrame(BaseModel):
    """The metric and periods an analysis is about.

    Fixed on the first query and reused by every drill-down step. Without it, a
    follow-up query could quietly change what "revenue" means or which months are
    being compared, and the drill-down would be explaining a different number.
    """

    metric_name: str = Field(description="e.g. 'Revenue'.")
    metric_sql: str = Field(
        description="The aggregate expression exactly as defined, e.g. "
        "\"SUM(o.total_amount) FILTER (WHERE o.status = 'SUCCESS')\"."
    )
    base_table: str = Field(description="Table the metric is computed from, e.g. 'orders'.")
    date_column: str | None = Field(
        default=None, description="Qualified date column used for period filters."
    )
    current_period: Period | None = None
    comparison_period: Period | None = None


class QueryAgentOutput(BaseModel):
    """A SQL query answering the question, or a refusal."""

    can_answer: bool = Field(
        description="False if the provided tables cannot answer the question."
    )
    cannot_answer_reason: str | None = Field(
        default=None, description="Why not, in one sentence. Required when can_answer is false."
    )
    sql: str | None = Field(
        default=None, description="One read-only SELECT. Null when can_answer is false."
    )
    explanation: str = Field(description="One or two sentences on what the query computes.")
    question_type: QuestionType | None = Field(
        default=None, description="Classify the question. Only on the first query of an analysis."
    )
    tables_used: list[str] = Field(default_factory=list)
    definitions_applied: list[str] = Field(
        default_factory=list,
        description="Names of business definitions applied, e.g. ['Revenue'].",
    )
    frame: AnalysisFrame | None = Field(
        default=None,
        description="Only for comparison and root_cause questions, on the first query.",
    )

    @model_validator(mode="after")
    def _answer_must_be_consistent(self) -> QueryAgentOutput:
        """Reject contradictory replies, so the client's repair step fixes them.

        A reply that says it can answer but supplies no SQL, or refuses without
        saying why, would otherwise travel on and fail somewhere harder to debug.
        """
        if self.can_answer and not (self.sql and self.sql.strip()):
            raise ValueError("sql is required when can_answer is true")
        if not self.can_answer and not self.cannot_answer_reason:
            raise ValueError("cannot_answer_reason is required when can_answer is false")
        return self
