"""The Analysis Agent node, and the planner that decides whether to act on its advice."""

from __future__ import annotations

import logging

from app.agents.analysis_agent import AnalysisRequest, QuerySummary, run_analysis_agent
from app.agents.schemas import DrillDownRequest
from app.analytics import ResultProfile
from app.analytics.fallback_findings import fallback_findings
from app.graph.deps import GraphDeps
from app.graph.drilldown import COMPARABLE_TYPES, DrillContext, check_drilldown
from app.graph.persistence import set_stage
from app.graph.state import AnalysisState, ExecutedQuery
from app.llm import LLMUnavailable
from app.llm.budget import BudgetExceeded

logger = logging.getLogger(__name__)


def _succeeded(state: AnalysisState) -> list[ExecutedQuery]:
    return [q for q in state.get("queries", []) if q.status == "succeeded"]


def _profile(query: ExecutedQuery) -> ResultProfile:
    return ResultProfile.model_validate(query.profile or {"shape": "empty", "row_count": 0})


def _reserve(deps: GraphDeps) -> int:
    """Calls kept back for the dashboard step, when that agent is enabled."""
    return 1 if deps.settings.viz_agent_enabled else 0


def _can_drill(state: AnalysisState, deps: GraphDeps) -> bool:
    """Whether another level is possible, so the model is not asked for the impossible."""
    frame = state.get("frame")
    return (
        state.get("question_type") in COMPARABLE_TYPES
        and frame is not None
        and frame.current_period is not None
        and frame.comparison_period is not None
        and state.get("drilldown_depth", 0) < deps.settings.max_drilldown_depth
        and deps.budget.remaining_calls - _reserve(deps) >= 2
    )


async def analysis_agent(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "analyzing")

    queries = _succeeded(state)
    request = AnalysisRequest(
        question=state["user_question"],
        question_type=state.get("question_type"),
        frame=state.get("frame"),
        queries=[
            QuerySummary(
                seq=q.seq, step_question=q.step_question, explanation=q.explanation,
                columns=q.columns, rows=q.rows, profile=_profile(q), filters=q.filters,
                dimension=q.dimension,
            )
            for q in queries
        ],
        dimensions=state.get("dimensions", []),
        used_dimensions=state.get("used_dimensions", []),
        depth=state.get("drilldown_depth", 0),
        max_depth=deps.settings.max_drilldown_depth,
        can_drill=_can_drill(state, deps),
    )

    try:
        result = await run_analysis_agent(deps.llm, request, budget=deps.budget)
    except (LLMUnavailable, BudgetExceeded) as exc:
        # The data exists and the statistics are computed. Reporting a failure would
        # discard a correct answer, so write a plain summary from the statistics.
        logger.warning("analysis agent unavailable, using fallback", extra={"error": str(exc)})
        last = queries[-1]
        return {
            "analysis_rounds": [
                fallback_findings(
                    _profile(last), seq=last.seq, frame=state.get("frame"),
                    reason="automatic summary because the analysis model was unavailable",
                    filters=last.filters,
                )
            ]
        }

    deps.llm_calls += result.llm_calls
    deps.tokens += result.usage.total
    return {"analysis_rounds": [result.value]}


async def plan_drilldown(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    """Act on the Analysis Agent's proposal, or end the investigation.

    The agent proposes; this decides. Every limit lives in `check_drilldown`, in code.
    """
    drafted = state["analysis_rounds"][-1].drilldown
    assert drafted is not None  # routing only sends proposals here

    queries = _succeeded(state)
    last = queries[-1]
    last_profile = _profile(last)
    segments = (
        [s.segment for s in last_profile.comparison.segments] if last_profile.comparison else []
    )

    # The model supplies the segment; the dimension that segment belongs to is whatever
    # the last breakdown was grouped by, which only the run knows. A headline figure has
    # no segments, so any focus the model offers there is meaningless and is dropped.
    focus: dict[str, str] = {}
    if drafted.focus_value and last.dimension and segments != ["total"]:
        focus = {last.dimension: str(drafted.focus_value)}

    proposal = DrillDownRequest(
        dimension=drafted.dimension, focus_filter=focus,
        step_question=drafted.step_question, rationale=drafted.rationale,
    )

    context = DrillContext(
        question_type=state.get("question_type"),
        frame=state.get("frame"),
        depth=state.get("drilldown_depth", 0),
        max_depth=deps.settings.max_drilldown_depth,
        remaining_calls=deps.budget.remaining_calls,
        remaining_tokens=deps.budget.max_tokens - deps.budget.total_tokens,
        used_dimensions=state.get("used_dimensions", []),
        filter_path=state.get("filter_path", []),
        previous_step_questions=[q.step_question for q in state.get("queries", [])],
        dimensions=state.get("dimensions", []),
        last_segments=segments,
        last_was_headline=segments == ["total"],
        reserve_calls=_reserve(deps),
    )
    verdict = check_drilldown(proposal, context)

    if not verdict.approved:
        logger.info("drill-down refused", extra={"reason": verdict.reason})
        # `drill` must be cleared: it still holds the previous approved step, and
        # leaving it would make the router start that step again.
        update: AnalysisState = {
            "drill": None,
            "notes": [verdict.note or f"A follow-up was proposed but not run: {verdict.reason}."],
        }
        if verdict.stop_reason:
            update["stop_reason"] = verdict.stop_reason  # type: ignore[typeddict-item]
        return update

    logger.info(
        "drill-down approved",
        extra={"dimension": proposal.dimension, "focus": proposal.focus_filter,
               "depth": state.get("drilldown_depth", 0) + 1},
    )
    filters = state.get("filter_path", [])
    return {
        "drill": proposal,
        "drilldown_depth": state.get("drilldown_depth", 0) + 1,
        "filter_path": [*filters, proposal.focus_filter] if proposal.focus_filter else filters,
        "used_dimensions": [*state.get("used_dimensions", []), proposal.dimension],
        "current_question": proposal.step_question,
        "mode": "drilldown",
        "attempt": 1,
        "last_error": None,
    }
