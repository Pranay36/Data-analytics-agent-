"""Closing a run: decide the outcome and record it."""

from __future__ import annotations

from app.graph.deps import GraphDeps
from app.graph.persistence import finish_analysis
from app.graph.state import AnalysisState, RunError

# Outcomes that are results, not failures. "I cannot answer that from this data"
# is a correct and useful reply, so the run is complete rather than failed.
_COMPLETED = {"answered", "cannot_answer"}


async def finalize(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    stop_reason = state.get("stop_reason")
    error = state.get("error")
    queries = state.get("queries", [])

    if stop_reason is None:
        last = queries[-1] if queries else None
        if last is not None and last.status == "succeeded":
            stop_reason = "answered"
        elif last is not None and last.status == "rejected":
            stop_reason = "invalid_sql"
            error = RunError(
                code="INVALID_SQL",
                message=f"Could not produce a safe query. Last problem: {last.error}",
            )
        else:
            stop_reason = "query_failed"
            error = RunError(
                code="QUERY_FAILED",
                message=f"The query kept failing. Last error: {last.error if last else 'unknown'}",
            )

    retrieved = state.get("retrieved")
    frame = state.get("frame")
    completed = stop_reason in _COMPLETED

    await finish_analysis(
        deps,
        state.get("analysis_id"),
        {
            "status": "completed" if completed else "failed",
            "stage": "done" if completed else "failed",
            "stop_reason": stop_reason,
            "question_type": state.get("question_type"),
            "analysis_frame": frame.model_dump(mode="json") if frame else None,
            "retrieved_context": retrieved.debug_payload() if retrieved else None,
            "llm_calls_count": deps.budget.calls,
            "total_input_tokens": deps.budget.input_tokens,
            "total_output_tokens": deps.budget.output_tokens,
            "error_code": error.code if error and not completed else None,
            "error_message": error.message if error else None,
        },
    )
    return {"stop_reason": stop_reason, "error": error}
