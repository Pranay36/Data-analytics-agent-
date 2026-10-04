"""Deciding whether a proposed drill-down is allowed.

The Analysis Agent *proposes* a next step; this module decides. Keeping that split
is what makes the loop safe. A model asked to "stop when you have enough" will
sometimes not, and a prompt is a request, not a guarantee. Every limit here is
enforced in code, so there is no model behaviour that can make the loop run on.

Pure functions: given the state they need and nothing else, returning a verdict.
That is what lets each rule be tested without running a graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.agents.schemas import AnalysisFrame, DrillDownRequest

# Enough for a Query Agent call and an Analysis Agent call at the next level.
CALLS_PER_LEVEL = 2
# Rough floor for one more level; a level costs about 7-8k tokens on a modest model.
MIN_TOKENS_PER_LEVEL = 12_000

COMPARABLE_TYPES = {"comparison", "root_cause"}


class DimensionInfo(BaseModel):
    """A column worth breaking a metric down by."""

    table: str
    column: str
    sample_values: list[str] = []
    description: str | None = None
    """From the catalog. Without it `customers.region` and `orders.shipping_region` look
    interchangeable, and the model picked the one that is not used for sales."""

    @property
    def name(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass
class Verdict:
    approved: bool
    reason: str = ""
    stop_reason: str | None = None
    """What the run should record when a request is refused."""
    note: str | None = None
    """A caveat to attach to the final answer, where one is useful."""


@dataclass
class DrillContext:
    question_type: str | None
    frame: AnalysisFrame | None
    depth: int
    max_depth: int
    remaining_calls: int
    remaining_tokens: int
    used_dimensions: list[str]
    filter_path: list[dict[str, str]]
    previous_step_questions: list[str]
    dimensions: list[DimensionInfo]
    last_segments: list[str]
    """Segment names from the most recent comparison result."""
    last_was_headline: bool = False
    """True when the last result was a single overall figure, with no segments yet.
    The first drill-down then breaks that total down, so it has nothing to focus on."""
    last_had_no_dominant: bool = False
    """True when the last breakdown had several segments and none stood out. There is
    nothing to focus on, so the useful next move is a different dimension on the same
    overall figure."""
    reserve_calls: int = 0
    """Held back for later steps, such as the dashboard."""
    extra: dict = field(default_factory=dict)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def check_drilldown(request: DrillDownRequest, ctx: DrillContext) -> Verdict:
    """Approve or refuse a proposed drill-down. Refusal ends the investigation.

    Checks run cheapest and most decisive first, so the recorded reason is the
    real one.
    """
    if ctx.question_type not in COMPARABLE_TYPES:
        return Verdict(False, f"{ctx.question_type!r} questions are not drilled into")

    if ctx.depth >= ctx.max_depth:
        return Verdict(
            False,
            f"maximum depth of {ctx.max_depth} reached",
            stop_reason="max_depth",
            note=f"The investigation stopped at the maximum depth of {ctx.max_depth} levels.",
        )

    if ctx.remaining_calls - ctx.reserve_calls < CALLS_PER_LEVEL:
        return Verdict(
            False,
            "not enough model calls left for another level",
            stop_reason="budget_exhausted",
            note="The investigation stopped early because the model-call budget was used up.",
        )
    if ctx.remaining_tokens < MIN_TOKENS_PER_LEVEL:
        return Verdict(
            False,
            "not enough token budget left for another level",
            stop_reason="budget_exhausted",
            note="The investigation stopped early because the token budget was used up.",
        )

    frame = ctx.frame
    if frame is None or frame.current_period is None or frame.comparison_period is None:
        # Without both periods a follow-up query cannot hold the comparison fixed,
        # and would be explaining a different number.
        return Verdict(False, "no comparison periods were established for this question")

    by_name = {dimension.name: dimension for dimension in ctx.dimensions}
    dimension = by_name.get(request.dimension)
    if dimension is None:
        return Verdict(False, f"{request.dimension!r} is not an available dimension")

    if request.dimension in ctx.used_dimensions:
        return Verdict(False, f"{request.dimension!r} has already been used")

    if request.dimension in request.focus_filter or any(
        request.dimension in filters for filters in ctx.filter_path
    ):
        # Breaking down by a column that is already filtered to one value would
        # return a single row and teach nothing.
        return Verdict(False, f"cannot break down by {request.dimension!r}: already filtered")

    if not request.focus_filter and not (ctx.last_was_headline or ctx.last_had_no_dominant):
        # After a breakdown there are segments to choose between; with none chosen
        # the follow-up would just repeat the same breakdown.
        return Verdict(False, "no segment was chosen to focus on")

    for column, value in request.focus_filter.items():
        filter_dimension = by_name.get(column)
        if filter_dimension is None:
            return Verdict(False, f"cannot filter on {column!r}: not an available dimension")
        known = set(ctx.last_segments) | set(filter_dimension.sample_values)
        if str(value) not in known:
            # An invented value would produce an empty result and a wasted level.
            return Verdict(False, f"{value!r} is not a known value of {column!r}")

    asked = _normalise(request.step_question)
    if any(_normalise(previous) == asked for previous in ctx.previous_step_questions):
        return Verdict(False, "this question has already been asked")

    return Verdict(True)
