"""Closing a run: decide the outcome and record it."""

from __future__ import annotations

from app.graph.deps import GraphDeps
from app.graph.persistence import finish_analysis
from app.graph.state import AnalysisState, RunError

# Outcomes that are results, not failures. "I cannot answer that from this data"
# is a correct and useful reply, so the run is complete rather than failed.
_COMPLETED = {"answered", "cannot_answer"}

# Outcomes that are a complete result only if something was actually found. Hitting
# a limit part-way through an investigation still leaves real findings; hitting it
# before any query succeeded leaves nothing.
_COMPLETED_IF_DATA = {"max_depth", "budget_exhausted", "inconclusive"}


async def finalize(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    stop_reason = state.get("stop_reason")
    error = state.get("error")
    queries = state.get("queries", [])

    if stop_reason is None:
        last = queries[-1] if queries else None
        if last is not None and last.status == "succeeded":
            stop_reason = "answered"
        elif (
            last is not None
            and last.purpose == "drilldown"
            and any(earlier.status == "succeeded" for earlier in queries)
        ):
            # A deeper step could not be produced or verified, but earlier steps
            # worked. Those findings stand, so report what was found and say where
            # the investigation stopped rather than failing the whole analysis.
            stop_reason = "inconclusive"
            notes = [
                *state.get("notes", []),
                "A deeper breakdown could not be verified, so the investigation "
                "stopped at the previous level.",
            ]
            state = {**state, "notes": notes}  # type: ignore[assignment]
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
    has_data = any(q.status == "succeeded" for q in queries)
    completed = stop_reason in _COMPLETED or (stop_reason in _COMPLETED_IF_DATA and has_data)

    findings = None
    if rounds := state.get("analysis_rounds"):
        findings = rounds[-1].model_dump(mode="json")
        findings["caveats"] = [*findings.get("caveats", []), *state.get("notes", [])]
        findings["rounds"] = len(rounds)

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
            "findings": findings,
            "drilldown_depth": state.get("drilldown_depth", 0),
            "llm_calls_count": deps.budget.calls,
            "total_input_tokens": deps.budget.input_tokens,
            "total_output_tokens": deps.budget.output_tokens,
            "error_code": error.code if error and not completed else None,
            "error_message": error.message if error else None,
        },
    )
    return {"stop_reason": stop_reason, "error": error}
