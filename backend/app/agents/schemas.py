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

    @model_validator(mode="after")
    def _a_frame_must_be_usable(self) -> QueryAgentOutput:
        """Comparison questions need a frame with both periods, or they cannot be drilled.

        Found by evaluation: on a "why did refunds increase" question the model returned
        no question type and a frame with no periods. The investigation then correctly
        refused to continue, since a follow-up cannot hold a comparison fixed without
        both periods, and the question got a one-line answer where it needed three levels.
        The fields are optional because most questions do not need them, which is exactly
        why a weaker model skips them. Rejecting the reply sends it through the existing
        repair step with a message that says what is missing.
        """
        has_periods = bool(
            self.frame and self.frame.current_period and self.frame.comparison_period
        )
        comparable = self.question_type in ("comparison", "root_cause")

        if self.can_answer and comparable and not has_periods:
            raise ValueError(
                "frame is required for comparison and root_cause questions, and must "
                "include both current_period and comparison_period"
            )
        # A frame with no periods is of no use to anything. Discard it quietly for other
        # question types rather than failing a question that never needed one.
        if self.frame is not None and not has_periods:
            self.frame = None
        return self


class Finding(BaseModel):
    statement: str = Field(
        description="One factual sentence using only numbers from the data provided."
    )
    evidence_query_seq: list[int] = Field(
        default_factory=list, description="Query numbers (seq) that support it."
    )
    importance: Literal["high", "medium", "low"] = "medium"


class DrillDownProposal(BaseModel):
    """What the Analysis Agent is asked for: which segment, and what to split it by.

    Deliberately small. It originally asked the model to build the whole filter, as
    `{"orders.shipping_region": "South"}`, and a lighter model wrote
    `{"orders": "shipping_region"}` instead. The code already knows which dimension
    the last breakdown used, so the model is asked only for the *value*. Every field
    it no longer has to get right is a way for it to fail that has been removed.
    """

    dimension: str = Field(
        description="Column to break down by, exactly as listed under AVAILABLE DIMENSIONS, "
        "e.g. 'products.category'."
    )
    focus_value: str | None = Field(
        default=None,
        description="The segment to investigate, exactly as shown in the data, e.g. 'South'. "
        "Leave empty when only an overall figure exists.",
    )
    step_question: str = Field(
        description="The follow-up question, e.g. 'How did revenue change by category "
        "within South, May vs June 2026?'"
    )
    rationale: str = Field(description="One sentence on why this is worth investigating.")


class DrillDownRequest(BaseModel):
    """A proposal resolved into something the guard and the Query Agent can act on.

    Built by code from a `DrillDownProposal` plus what the run already knows. A
    request only; whether it is acted on is decided by deterministic code, which
    checks depth, budget, and that the dimension and value really exist.
    """

    dimension: str = Field(
        description="Column to break down by, exactly as listed under AVAILABLE DIMENSIONS, "
        "e.g. 'products.category'."
    )
    focus_filter: dict[str, str] = Field(
        description="The segment to restrict to, as {dimension: value}, "
        "e.g. {'orders.shipping_region': 'South'}."
    )
    step_question: str = Field(
        description="The follow-up question, e.g. 'How did revenue change by category "
        "within South, May vs June 2026?'"
    )
    rationale: str = Field(description="One sentence on why this is worth investigating.")


class AnalysisAgentOutput(BaseModel):
    summary: str = Field(
        description="The headline answer to the user's question, one to three sentences."
    )
    findings: list[Finding] = Field(default_factory=list, max_length=6)
    needs_drilldown: bool = False
    drilldown: DrillDownProposal | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _flag_and_proposal_agree(self) -> AnalysisAgentOutput:
        if self.needs_drilldown and self.drilldown is None:
            raise ValueError("drilldown is required when needs_drilldown is true")

        # Found by evaluation: the model wrote a complete, valid proposal for the next
        # step, with a rationale, and still set `needs_drilldown` to false, so a
        # three-level investigation silently stopped at one. A concrete proposal is far
        # stronger evidence of intent than a boolean, so it wins. Nothing is risked by
        # this: the proposal still has to pass the deterministic guard, which refuses
        # anything invalid, repeated or beyond the limits.
        if self.drilldown is not None and not self.needs_drilldown:
            self.needs_drilldown = True
        return self
