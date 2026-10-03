"""The Query Agent node."""

from __future__ import annotations

import logging

from app.agents.query_agent import QueryRequest, run_query_agent
from app.graph.deps import GraphDeps
from app.graph.persistence import set_stage
from app.graph.state import AnalysisState, RunError
from app.llm import LLMUnavailable
from app.llm.budget import BudgetExceeded

logger = logging.getLogger(__name__)


async def query_agent(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    attempt = state["attempt"]
    await set_stage(
        deps, state.get("analysis_id"), "generating_sql" if attempt == 1 else "repairing_sql"
    )

    datasource = state["datasource"]
    repairing = bool(state.get("last_error"))
    previous = state.get("pending")

    request = QueryRequest(
        question=state["current_question"],
        context=state["retrieved"],
        dialect=datasource.dialect,
        as_of_date=datasource.as_of_date,
        currency=datasource.currency,
        previous_sql=previous.sql if repairing and previous else None,
        previous_error=state.get("last_error") if repairing else None,
        frame=state.get("frame") if state["mode"] == "drilldown" else None,
    )

    try:
        result = await run_query_agent(deps.llm, request, budget=deps.budget)
    except BudgetExceeded as exc:
        return {
            "stop_reason": "budget_exhausted",
            "error": RunError(code="BUDGET_EXHAUSTED", message=str(exc)),
        }
    except LLMUnavailable as exc:
        logger.warning("all models failed", extra={"failures": exc.failures})
        return {
            "stop_reason": "llm_unavailable",
            "error": RunError(
                code="LLM_UNAVAILABLE",
                message="No language model could answer right now. "
                + (exc.failures[-1] if exc.failures else ""),
            ),
        }

    deps.llm_calls += result.llm_calls
    deps.tokens += result.usage.total
    output = result.value

    update: AnalysisState = {"pending": output, "last_error": None}

    # The first query of an analysis fixes what the question *is*. Later queries,
    # including repairs, must not redefine it.
    if state["mode"] == "primary" and state.get("question_type") is None:
        update["question_type"] = output.question_type
        update["frame"] = output.frame

    if not output.can_answer:
        update["stop_reason"] = "cannot_answer"
        update["error"] = RunError(
            code="CANNOT_ANSWER", message=output.cannot_answer_reason or "Cannot answer."
        )
    return update
