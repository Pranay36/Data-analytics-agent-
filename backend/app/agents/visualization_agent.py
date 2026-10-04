"""The Visualization Agent: chooses how to present an analysis.

The third LLM step. It decides which results become which widgets and how to title and
describe them. It is shown column names, inferred types and a few sample rows, never
the full data, and it cannot supply a number: widgets point at queries, and values are
read from the results. What it produces is a *draft*; `validate_dashboard` checks every
claim in it against the real data before anything is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.schemas import AnalysisAgentOutput
from app.analytics import ResultProfile, render_profile
from app.dashboard import DashboardSpec, column_kind
from app.llm import CallBudget, ChatMessage, LLMClient, StructuredResult

PROMPT = Path(__file__).parent / "prompts" / "visualization_agent.md"
SAMPLE_ROWS = 5


@dataclass
class VisualizationRequest:
    question: str
    analysis: AnalysisAgentOutput | None
    queries: list[Any]
    """Executed queries; only the succeeded ones are shown."""


def _describe(query: Any) -> str:
    kinds = {name: column_kind(query.rows, index) for index, name in enumerate(query.columns)}
    columns = ", ".join(f"{name} ({kind})" for name, kind in kinds.items())
    profile = ResultProfile.model_validate(query.profile or {"shape": "empty", "row_count": 0})

    sample = "\n".join(
        " | ".join("" if v is None else str(v) for v in row) for row in query.rows[:SAMPLE_ROWS]
    )
    more = (
        f"\n... {query.row_count - SAMPLE_ROWS} more rows" if query.row_count > SAMPLE_ROWS else ""
    )
    filters = f" Filters: {query.filters}." if query.filters else ""
    by = f" Broken down by {query.dimension}." if query.dimension else ""

    return (
        f"## QUERY {query.seq}\n"
        f"{query.step_question}{by}{filters}\n"
        f"Columns: {columns}\nRows: {query.row_count}\n"
        f"Statistics: {render_profile(profile)}\n"
        f"Sample:\n{sample}{more}"
    )


def build_messages(request: VisualizationRequest) -> list[ChatMessage]:
    parts = [f"## QUESTION\n{request.question}"]

    if request.analysis is not None:
        analysis = request.analysis
        findings = "\n".join(f"- {f.statement}" for f in analysis.findings)
        parts.append(f"## ANALYSIS\n{analysis.summary}\n{findings}")

    parts += [_describe(q) for q in request.queries if q.status == "succeeded"]

    return [
        ChatMessage(role="system", content=PROMPT.read_text(encoding="utf-8").strip()),
        ChatMessage(role="user", content="\n\n".join(parts)),
    ]


async def run_visualization_agent(
    client: LLMClient, request: VisualizationRequest, *, budget: CallBudget | None = None
) -> StructuredResult[DashboardSpec]:
    return await client.generate_structured(
        agent="visualization",
        messages=build_messages(request),
        schema=DashboardSpec,
        schema_name="submit_dashboard",
        temperature=0.2,
        max_tokens=2000,
        budget=budget,
    )
