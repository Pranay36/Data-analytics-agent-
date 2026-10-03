"""The Query Agent: turns a question and its retrieved context into SQL.

This is one of only three places an LLM is used. It does exactly one thing — write
a query — and everything around it is deterministic: retrieval before, validation
and execution after. Keeping it narrow is what makes its failures diagnosable: a
wrong answer is traceable to either the context it was given or the way it used
that context, never to a tangle of both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.agents.schemas import AnalysisFrame, DrillDownRequest, QueryAgentOutput
from app.llm import CallBudget, ChatMessage, LLMClient, StructuredResult
from app.rag import RetrievedContext, render_context

PROMPTS = Path(__file__).parent / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def dialect_guidance(dialect: str) -> str:
    """Dialect notes for the engine in use.

    The connector reports its dialect, so the agent layer never contains a
    database-specific branch of its own: supporting ClickHouse later means adding
    a file here, not editing this one.
    """
    path = PROMPTS / "dialects" / f"{dialect}.md"
    if not path.exists():
        return f"## SQL dialect: {dialect}\n\nUse standard SQL for {dialect}."
    return path.read_text(encoding="utf-8").strip()


@dataclass
class QueryRequest:
    question: str
    context: RetrievedContext
    dialect: str
    as_of_date: date
    currency: str | None = None
    # Repair: the previous attempt and why it failed.
    previous_sql: str | None = None
    previous_error: str | None = None
    # Drill-down: the metric and periods to hold fixed, and the step to carry out.
    frame: AnalysisFrame | None = None
    drill: DrillDownRequest | None = None
    filters: list[dict[str, str]] = field(default_factory=list)
    """Every segment restriction accumulated down the path, not just the latest."""

    @property
    def is_drilldown(self) -> bool:
        return self.drill is not None and self.frame is not None

    @property
    def mode(self) -> str:
        if self.previous_sql is not None:
            return "repair"
        return "drilldown" if self.is_drilldown else "primary"


def build_messages(request: QueryRequest) -> list[ChatMessage]:
    system = "\n\n".join([_read("query_agent.md"), dialect_guidance(request.dialect)])

    header = [f"Today is {request.as_of_date.isoformat()}."]
    if request.currency:
        header.append(f"Currency: {request.currency}.")

    parts = [
        " ".join(header),
        render_context(request.context),
        f"## QUESTION\n{request.question}",
    ]

    if request.is_drilldown:
        parts.append(_drilldown_block(request))
    else:
        parts.append(
            "## THIS IS THE FIRST QUERY OF THE ANALYSIS\n"
            "Also set `question_type`.\n"
            "For a comparison or a why-question about a change, set `frame` with the "
            "metric's exact SQL expression and the two periods being compared (half-open "
            "date ranges), so follow-up queries can reuse them unchanged. Then make this "
            "query the HEADLINE: a single row with columns `previous_value` and "
            "`current_value` — the metric over the earlier period and over the later one. "
            "Do not break it down yet; later steps will."
        )

    if request.previous_sql is not None:
        parts.append(
            "## YOUR PREVIOUS QUERY FAILED\n"
            f"Query:\n{request.previous_sql}\n\nProblem: {request.previous_error}\n\n"
            "Fix exactly that problem. Keep the intent of the query the same."
        )

    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]


def _drilldown_block(request: QueryRequest) -> str:
    frame, drill = request.frame, request.drill
    assert frame is not None and drill is not None

    current, previous = frame.current_period, frame.comparison_period
    periods = ""
    if current and previous:
        periods = (
            f"- Previous period ({previous.label}): "
            f"{previous.start.isoformat()} up to but excluding {previous.end.isoformat()}.\n"
            f"- Current period ({current.label}): "
            f"{current.start.isoformat()} up to but excluding {current.end.isoformat()}.\n"
        )

    if request.filters:
        merged = {column: value for step in request.filters for column, value in step.items()}
        restriction = "; ".join(f"{column} = '{value}'" for column, value in merged.items())
        filters = f"- Restrict to: {restriction}. Apply every one as a WHERE condition.\n"
    else:
        filters = "- No restriction: break down the overall figure.\n"

    return (
        "## THIS IS A DRILL-DOWN STEP\n"
        "Keep this consistent with the earlier queries: the same metric, the same "
        "periods. Do NOT change what is being measured.\n"
        f"- Metric: {frame.metric_name}. In the earlier step it was computed as "
        f"{frame.metric_sql} on the {frame.base_table} table.\n"
        f"{periods}{filters}"
        f"- Break the result down by: {drill.dimension}\n"
        "\n"
        "IMPORTANT: the breakdown must add up to the figure it breaks down. If "
        f"{drill.dimension} lives on a table that must be reached through a one-to-many "
        "join (for example order line items), do NOT sum the parent table's total "
        "across that join: every order would be counted once per line. Use the "
        "line-level equivalent given in the BUSINESS DEFINITIONS, with the same status "
        "filter and periods.\n"
        "\n"
        "Return EXACTLY these columns, in this order: `segment` (the value of "
        f"{drill.dimension}), `previous_value`, `current_value`. "
        "Do NOT add an ORDER BY: the results are sorted for you. (Ordering by the "
        "aliases `previous_value` or `current_value` fails in PostgreSQL, and cost a "
        "repair call at every level when it was requested.)"
    )


async def run_query_agent(
    client: LLMClient,
    request: QueryRequest,
    *,
    budget: CallBudget | None = None,
) -> StructuredResult[QueryAgentOutput]:
    return await client.generate_structured(
        agent="query",
        messages=build_messages(request),
        schema=QueryAgentOutput,
        schema_name="submit_query",
        temperature=0.0,
        max_tokens=1500,
        budget=budget,
    )
