"""The Query Agent: turns a question and its retrieved context into SQL.

This is one of only three places an LLM is used. It does exactly one thing — write
a query — and everything around it is deterministic: retrieval before, validation
and execution after. Keeping it narrow is what makes its failures diagnosable: a
wrong answer is traceable to either the context it was given or the way it used
that context, never to a tangle of both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.agents.schemas import AnalysisFrame, QueryAgentOutput
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
    # Drill-down: the metric and periods to hold fixed.
    frame: AnalysisFrame | None = None

    @property
    def mode(self) -> str:
        if self.previous_sql is not None:
            return "repair"
        if self.frame is not None:
            return "drilldown"
        return "primary"


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

    if request.mode == "primary":
        parts.append(
            "## THIS IS THE FIRST QUERY OF THE ANALYSIS\n"
            "Also set `question_type`. For a comparison or a why-question, set `frame` "
            "with the metric's exact SQL expression and the two periods being compared, "
            "so that follow-up queries can reuse it unchanged."
        )
    elif request.mode == "repair":
        parts.append(
            "## YOUR PREVIOUS QUERY FAILED\n"
            f"Query:\n{request.previous_sql}\n\nProblem: {request.previous_error}\n\n"
            "Fix exactly that problem. Keep the intent of the query the same."
        )

    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]


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
