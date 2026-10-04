"""Building the dashboard: the Visualization Agent proposes, code validates and stores.

The model may be switched off, rate-limited or wrong, and none of those should cost the
user a result: the data has been fetched and the statistics computed, so a dashboard can
always be built from rules. The model's draft is used only if enough of it survives
validation to be worth showing.
"""

from __future__ import annotations

import logging

from app.agents.visualization_agent import VisualizationRequest, run_visualization_agent
from app.dashboard import fallback_dashboard, validate_dashboard
from app.graph.deps import GraphDeps
from app.graph.persistence import save_dashboard, set_stage
from app.graph.state import AnalysisState
from app.llm import LLMUnavailable
from app.llm.budget import BudgetExceeded

logger = logging.getLogger(__name__)


async def visualization_agent(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "building_dashboard")

    queries = state.get("queries", [])
    rounds = state.get("analysis_rounds") or []
    request = VisualizationRequest(
        question=state["user_question"],
        analysis=rounds[-1] if rounds else None,
        queries=queries,
    )

    # Switched off while developing to save free-tier quota; a rule-built dashboard
    # takes over, and this agent is still tested.
    if not deps.settings.viz_agent_enabled or deps.budget.remaining_calls < 1:
        return {"dashboard_draft": None}

    try:
        result = await run_visualization_agent(deps.llm, request, budget=deps.budget)
    except (LLMUnavailable, BudgetExceeded) as exc:
        logger.warning("visualization agent unavailable", extra={"error": str(exc)})
        return {"dashboard_draft": None}

    deps.llm_calls += result.llm_calls
    deps.tokens += result.usage.total
    return {"dashboard_draft": result.value}


async def build_dashboard(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    queries = state.get("queries", [])
    rounds = state.get("analysis_rounds") or []
    analysis = rounds[-1] if rounds else None

    outcome = None
    source = "fallback"

    if (draft := state.get("dashboard_draft")) is not None:
        candidate = validate_dashboard(draft, queries)
        # A draft that kept only the auto-added data table has nothing of the model's in it.
        if candidate.spec.kpis or candidate.spec.charts:
            outcome, source = candidate, "llm"
        else:
            logger.info("dashboard draft discarded", extra={"dropped": candidate.dropped})

    if outcome is None:
        spec = fallback_dashboard(state["user_question"], queries, analysis, state.get("frame"))
        outcome = validate_dashboard(spec, queries)

    await save_dashboard(deps, state.get("analysis_id"), outcome.spec, source)
    return {
        "dashboard": outcome.spec,
        "dashboard_source": source,
        "dashboard_dropped": outcome.dropped,
    }
