"""Where the graph goes next.

Pure functions of state, kept apart from the nodes so each decision can be tested
without running anything. Every loop here is bounded by a counter held in state,
so there is no path that can run forever.
"""

from __future__ import annotations

from typing import Literal

from app.graph.state import AnalysisState

QueryNext = Literal["validate_sql", "finalize"]
ValidateNext = Literal["execute_sql", "query_agent", "finalize"]
ExecuteNext = Literal["finalize", "query_agent"]


def after_load(state: AnalysisState) -> Literal["retrieve_context", "finalize"]:
    return "finalize" if state.get("stop_reason") else "retrieve_context"


def after_retrieve(state: AnalysisState) -> Literal["query_agent", "finalize"]:
    return "finalize" if state.get("stop_reason") else "query_agent"


def after_query(state: AnalysisState) -> QueryNext:
    """Straight to the end if the model refused or the LLM layer failed."""
    if state.get("stop_reason"):
        return "finalize"
    pending = state.get("pending")
    if pending is None or not pending.can_answer or not pending.sql:
        return "finalize"
    return "validate_sql"


def after_validate(state: AnalysisState, *, max_attempts: int) -> ValidateNext:
    if state.get("stop_reason"):
        return "finalize"
    if state["validation"].ok:
        return "execute_sql"
    # Rejected. `attempt` was already advanced by the validator, so exceeding the
    # limit here means every allowed try has been used.
    return "query_agent" if state["attempt"] <= max_attempts else "finalize"


def after_execute(state: AnalysisState, *, max_attempts: int) -> ExecuteNext:
    if state.get("stop_reason"):
        return "finalize"
    if state.get("last_error") and state["attempt"] <= max_attempts:
        return "query_agent"
    return "finalize"
