"""The Analysis Agent: interprets results and decides whether to dig deeper.

The second of the three LLM steps. It is given statistics that were computed in
code, so its job is interpretation and judgement — what do these numbers mean, and
is another breakdown worth running — not arithmetic. It *proposes* a drill-down; the
deterministic guard in `app.graph.drilldown` decides whether to act on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agents.schemas import AnalysisAgentOutput, AnalysisFrame
from app.analytics import ResultProfile, render_profile
from app.graph.drilldown import DimensionInfo
from app.llm import CallBudget, ChatMessage, LLMClient, StructuredResult

PROMPT = Path(__file__).parent / "prompts" / "analysis_agent.md"
MAX_ROWS_SHOWN = 12


UNEQUAL_PERIOD_TOLERANCE = 0.10


def period_length_warning(frame: AnalysisFrame | None) -> str | None:
    """A warning when the two compared periods are not the same length.

    Found by evaluation: asked why refunds rose in May and June compared with earlier
    months, the system compared four months of totals with two, concluded refunds had
    *fallen* 12.6%, and told the user their premise was wrong. Every number it quoted
    was real, so the groundedness check passed. Whether two periods are the same length
    is arithmetic on dates the run already holds, so code states it rather than hoping
    the model notices.
    """
    if frame is None or frame.current_period is None or frame.comparison_period is None:
        return None
    current = (frame.current_period.end - frame.current_period.start).days
    previous = (frame.comparison_period.end - frame.comparison_period.start).days
    if current <= 0 or previous <= 0:
        return None
    if abs(current - previous) / max(current, previous) <= UNEQUAL_PERIOD_TOLERANCE:
        return None
    return (
        f"The periods compared differ in length: {frame.comparison_period.label} is "
        f"{previous} days and {frame.current_period.label} is {current} days, so their "
        "totals are not directly comparable."
    )


@dataclass
class QuerySummary:
    """One executed query, reduced to what the analyst needs to read."""

    seq: int
    step_question: str
    explanation: str
    columns: list[str]
    rows: list[list]
    profile: ResultProfile
    filters: list[dict[str, str]] = field(default_factory=list)
    dimension: str | None = None
    """What this query broke the metric down by. The model needs it to write the
    focus filter for the next step: South is a value of orders.shipping_region, and
    only the query knows that."""


@dataclass
class AnalysisRequest:
    question: str
    question_type: str | None
    frame: AnalysisFrame | None
    queries: list[QuerySummary]
    dimensions: list[DimensionInfo]
    used_dimensions: list[str]
    depth: int
    max_depth: int
    can_drill: bool
    """False once limits are reached, so the model is not told to do the impossible."""


def _format_rows(columns: list[str], rows: list[list]) -> str:
    header = " | ".join(columns)
    body = [" | ".join("" if v is None else _cell(v) for v in row) for row in rows[:MAX_ROWS_SHOWN]]
    more = f"\n... and {len(rows) - MAX_ROWS_SHOWN} more rows" if len(rows) > MAX_ROWS_SHOWN else ""
    return header + "\n" + "\n".join(body) + more


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def build_messages(request: AnalysisRequest) -> list[ChatMessage]:
    parts = [f"## QUESTION\n{request.question}"]
    if request.question_type:
        parts.append(f"Question type: {request.question_type}")

    if request.frame is not None:
        frame = request.frame
        periods = ""
        if frame.current_period and frame.comparison_period:
            periods = (
                f"  Comparing {frame.current_period.label} against "
                f"{frame.comparison_period.label}."
            )
        parts.append(f"Metric: {frame.metric_name}.{periods}")
        if warning := period_length_warning(frame):
            parts.append(f"WARNING: {warning} Do not conclude a rise or fall from the raw totals.")

    for position, query in enumerate(request.queries):
        # Only the most recent result needs its raw rows. The statistics already carry
        # what earlier ones showed, and resending every row at each level made the
        # prompt grow with depth: a full investigation spent most of its tokens
        # repeating earlier tables.
        is_latest = position == len(request.queries) - 1
        filters = f" Filters: {query.filters}." if query.filters else ""
        broken_down = f"\nBroken down by: {query.dimension}" if query.dimension else ""
        parts.append(
            f"## QUERY {query.seq}\n"
            f"Question: {query.step_question}\n"
            f"Computes: {query.explanation}{filters}{broken_down}\n\n"
            f"Statistics:\n{render_profile(query.profile)}"
            + (f"\n\nRows:\n{_format_rows(query.columns, query.rows)}" if is_latest else "")
        )

    if request.can_drill:
        available = [d for d in request.dimensions if d.name not in request.used_dimensions]
        lines = []
        for d in available:
            values = f" (values: {', '.join(d.sample_values[:6])})" if d.sample_values else ""
            note = f" - {d.description[:140]}" if d.description else ""
            lines.append(f"- {d.name}{values}{note}")
        parts.append(
            f"## DRILL-DOWN STATUS\nDepth used: {request.depth} of {request.max_depth}.\n"
            f"Already used: {', '.join(request.used_dimensions) or 'none'}.\n\n"
            "## AVAILABLE DIMENSIONS\n" + "\n".join(lines)
        )
    else:
        parts.append(
            "## DRILL-DOWN STATUS\nNo further drill-down is possible. "
            "Set needs_drilldown to false and summarise what the data shows."
        )

    return [
        ChatMessage(role="system", content=PROMPT.read_text(encoding="utf-8").strip()),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]


async def run_analysis_agent(
    client: LLMClient, request: AnalysisRequest, *, budget: CallBudget | None = None
) -> StructuredResult[AnalysisAgentOutput]:
    return await client.generate_structured(
        agent="analysis",
        messages=build_messages(request),
        schema=AnalysisAgentOutput,
        schema_name="submit_analysis",
        temperature=0.1,
        max_tokens=1500,
        budget=budget,
    )
