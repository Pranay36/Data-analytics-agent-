"""Assembling the analysis graph.

The shape of the workflow lives here and nowhere else, so what the system does can
be read in one place:

    load_context -> retrieve_context -> query_agent -> validate_sql -> execute_sql
                          ^                  ^               |              |
                          |                  +--- repair ----+--------------+
                          |                                                 v
                          |                                          analysis_agent
                          |                                                 |
                          +------------- approved ---------- plan_drilldown-+
                                                                    |  refused / done
                                                                    v
                                                                finalize

Two loops, both bounded in code. Repair is limited by the attempt counter. The
investigation loop is limited by depth, budget and the drill-down guard, which
refuses anything that would not be a genuinely new, valid step.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph import routing
from app.graph.deps import GraphDeps
from app.graph.nodes import (
    analysis_agent,
    execute_sql,
    finalize,
    load_context,
    plan_drilldown,
    query_agent,
    retrieve_context,
    validate_sql_node,
)
from app.graph.state import AnalysisState

# A backstop only: the attempt counter ends every loop long before this. If it is
# ever reached, a routing bug has made a loop unbounded, and failing loudly beats
# spinning.
RECURSION_LIMIT = 40


def build_graph(deps: GraphDeps):
    def bind(node):
        # Nodes are `(state, deps)`; LangGraph passes only state.
        async def run(state: AnalysisState) -> AnalysisState:
            return await node(state, deps)

        run.__name__ = node.__name__
        return run

    graph = StateGraph(AnalysisState)
    graph.add_node("load_context", bind(load_context))
    graph.add_node("retrieve_context", bind(retrieve_context))
    graph.add_node("query_agent", bind(query_agent))
    graph.add_node("validate_sql", bind(validate_sql_node))
    graph.add_node("execute_sql", bind(execute_sql))
    graph.add_node("analysis_agent", bind(analysis_agent))
    graph.add_node("plan_drilldown", bind(plan_drilldown))
    graph.add_node("finalize", bind(finalize))

    graph.add_edge(START, "load_context")
    graph.add_conditional_edges("load_context", routing.after_load)
    graph.add_conditional_edges("retrieve_context", routing.after_retrieve)
    graph.add_conditional_edges("query_agent", routing.after_query)
    graph.add_conditional_edges(
        "validate_sql", partial(routing.after_validate, max_attempts=deps.max_attempts)
    )
    graph.add_conditional_edges(
        "execute_sql", partial(routing.after_execute, max_attempts=deps.max_attempts)
    )
    graph.add_conditional_edges("analysis_agent", routing.after_analysis)
    graph.add_conditional_edges("plan_drilldown", routing.after_plan)
    graph.add_edge("finalize", END)

    return graph.compile()
