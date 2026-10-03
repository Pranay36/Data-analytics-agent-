"""The investigation loop, end to end.

The model is scripted but everything else is real: the queries run against the demo
database, the profiler computes the statistics, and the guard decides whether each
proposed drill-down is allowed. So the planted pattern (South, then Electronics) is
genuinely discovered from data, not asserted.

The adversarial cases matter most. A loop that terminates only when the model is
well-behaved is not bounded; these show it terminating when the model is not.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.analytics import ResultProfile
from app.db.models import Analysis, AnalysisQuery
from app.db.session import get_sessionmaker
from app.llm.errors import ModelUnavailable
from app.services.analysis_runner import run_analysis

from .conftest import EMBEDDINGS, analysis, reply, scripted_llm

pytestmark = pytest.mark.integration

MAY = "order_date >= DATE '2026-05-01' AND order_date < DATE '2026-06-01'"
JUNE = "order_date >= DATE '2026-06-01' AND order_date < DATE '2026-07-01'"
BOTH = "order_date >= DATE '2026-05-01' AND order_date < DATE '2026-07-01'"

HEADLINE_SQL = f"""
SELECT SUM(total_amount) FILTER (WHERE {MAY}) AS previous_value,
       SUM(total_amount) FILTER (WHERE {JUNE}) AS current_value
FROM orders WHERE status = 'SUCCESS' AND {BOTH}"""

REGION_SQL = f"""
SELECT shipping_region AS segment,
       SUM(total_amount) FILTER (WHERE {MAY}) AS previous_value,
       SUM(total_amount) FILTER (WHERE {JUNE}) AS current_value
FROM orders WHERE status = 'SUCCESS' AND {BOTH}
GROUP BY shipping_region ORDER BY 3 - 2"""

CATEGORY_SQL = f"""
SELECT p.category AS segment,
       SUM(oi.line_amount) FILTER (WHERE o.{MAY}) AS previous_value,
       SUM(oi.line_amount) FILTER (WHERE o.{JUNE}) AS current_value
FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id
WHERE o.status = 'SUCCESS' AND o.shipping_region = 'South' AND o.{BOTH}
GROUP BY p.category"""

FRAME = {
    "metric_name": "Revenue", "metric_sql": "SUM(total_amount)", "base_table": "orders",
    "date_column": "orders.order_date",
    "current_period": {"start": "2026-06-01", "end": "2026-07-01", "label": "June 2026"},
    "comparison_period": {"start": "2026-05-01", "end": "2026-06-01", "label": "May 2026"},
}

QUESTION = "Why did revenue fall in June 2026?"


def headline() -> str:
    return reply(sql=HEADLINE_SQL, question_type="root_cause", frame=FRAME,
                 explanation="Revenue May vs June")


def by_region() -> str:
    return analysis(
        summary="Revenue fell 11.4%.", needs_drilldown=True,
        drilldown={"dimension": "orders.shipping_region", "focus_value": None,
                   "step_question": "How did revenue change by region, May vs June 2026?",
                   "rationale": "See where the fall came from."},
    )


def by_category_in_south() -> str:
    return analysis(
        summary="South fell 35%, 85% of the decline.", needs_drilldown=True,
        drilldown={"dimension": "products.category", "focus_value": "South",
                   "step_question": "How did revenue change by category within South?",
                   "rationale": "South dominates."},
    )


async def run(source, *replies):
    client, provider = scripted_llm(*replies)
    result = await run_analysis(source, QUESTION, llm=client, embeddings=EMBEDDINGS)
    return result, provider


async def row(analysis_id: str) -> Analysis:
    async with get_sessionmaker()() as session:
        return await session.get(Analysis, analysis_id)


# ── The headline scenario ────────────────────────────────────────────────────
async def test_it_drills_from_the_total_to_south_to_electronics(source) -> None:
    result, provider = await run(
        source,
        headline(), by_region(),
        reply(sql=REGION_SQL, explanation="Revenue by region"), by_category_in_south(),
        reply(sql=CATEGORY_SQL, explanation="South revenue by category"),
        analysis(summary="Electronics in South fell sharply.", confidence="high"),
    )
    state = result.state

    assert state["stop_reason"] == "answered"
    assert state["drilldown_depth"] == 2
    assert len(provider.requests) == 6, "three queries and three analyses, no more"

    steps = [q for q in state["queries"] if q.status == "succeeded"]
    assert [q.purpose for q in steps] == ["primary", "drilldown", "drilldown"]

    # The profiler found the pattern in real data; nothing here was asserted by a script.
    regions = ResultProfile.model_validate(steps[1].profile).comparison
    assert regions.dominant_segment == "South"
    assert regions.segments[0].share_of_total_change > 0.8

    categories = ResultProfile.model_validate(steps[2].profile).comparison
    assert categories.dominant_segment == "Electronics"


async def test_filters_accumulate_down_the_path(source) -> None:
    result, _ = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(), reply(sql=CATEGORY_SQL), analysis(),
    )
    steps = [q for q in result.state["queries"] if q.status == "succeeded"]

    assert steps[0].filters == []
    assert steps[1].filters == [] and steps[1].dimension == "orders.shipping_region"
    assert steps[2].filters == [{"orders.shipping_region": "South"}]
    assert steps[2].dimension == "products.category"


async def test_the_drill_step_is_told_exactly_what_to_hold_fixed(source) -> None:
    """Otherwise a follow-up could quietly explain a different number."""
    _, provider = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(), reply(sql=CATEGORY_SQL), analysis(),
    )
    drill_prompt = provider.requests[4].messages[-1].content

    assert "DRILL-DOWN STEP" in drill_prompt
    assert "SUM(total_amount)" in drill_prompt, "the metric expression is fixed"
    assert "2026-05-01" in drill_prompt and "2026-07-01" in drill_prompt
    assert "orders.shipping_region = 'South'" in drill_prompt
    assert "products.category" in drill_prompt


async def test_the_analysis_agent_is_given_computed_statistics(source) -> None:
    _, provider = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), analysis(summary="South dominates."),
    )
    prompt = provider.requests[3].messages[-1].content

    assert "85% of the overall change" in prompt
    assert "Dominant segment: South" in prompt


async def test_the_whole_investigation_is_recorded(source) -> None:
    result, _ = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(), reply(sql=CATEGORY_SQL),
        analysis(summary="Electronics in South fell sharply."),
    )
    stored = await row(result.analysis_id)

    assert stored.status == "completed" and stored.drilldown_depth == 2
    assert stored.question_type == "root_cause"
    assert stored.findings["summary"] == "Electronics in South fell sharply."

    async with get_sessionmaker()() as session:
        queries = (await session.scalars(
            select(AnalysisQuery).where(AnalysisQuery.analysis_id == result.analysis_id)
            .order_by(AnalysisQuery.seq))).all()
    assert [q.purpose for q in queries] == ["primary", "drilldown", "drilldown"]
    assert queries[2].filters == [{"orders.shipping_region": "South"}]
    assert queries[1].profile["comparison"]["dominant_segment"] == "South"


# ── The loop must end, however the model behaves ─────────────────────────────
async def test_a_model_that_never_stops_asking_is_stopped_at_the_depth_limit(
    source, monkeypatch
) -> None:
    from app.core.config import get_settings
    monkeypatch.setenv("MAX_DRILLDOWN_DEPTH", "2")
    get_settings.cache_clear()

    greedy = analysis(
        needs_drilldown=True,
        drilldown={"dimension": "orders.channel", "focus_value": "Electronics",
                   "step_question": "Break it down by channel?", "rationale": "more"},
    )
    result, provider = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(), reply(sql=CATEGORY_SQL),
        greedy,   # asks for a third level; the limit is 2
    )
    state = result.state

    assert state["drilldown_depth"] == 2
    assert state["stop_reason"] == "max_depth"
    assert len(provider.requests) == 6, "no seventh call: the request was refused, not honoured"
    assert any("maximum depth" in note for note in state["notes"])

    stored = await row(result.analysis_id)
    assert stored.status == "completed", "stopping at a limit with findings is still a result"
    assert any("maximum depth" in c for c in stored.findings["caveats"])


async def test_an_invented_segment_ends_the_investigation_not_the_run(source) -> None:
    """An invented value would run, return nothing and waste a level."""
    bad = analysis(
        needs_drilldown=True,
        drilldown={"dimension": "products.category", "focus_value": "Atlantis",
                   "step_question": "Look inside Atlantis?", "rationale": "x"},
    )
    result, provider = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), bad,
    )

    assert result.state["stop_reason"] == "answered"
    assert len(provider.requests) == 4, "refused, so no further query was attempted"
    assert any("Atlantis" in n for n in result.state["notes"])


async def test_the_focus_dimension_is_taken_from_the_breakdown_not_from_the_model(source) -> None:
    """The model said only "South". Which column South belongs to is known from the
    breakdown that was just run, so the model cannot get it wrong."""
    result, _ = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(), reply(sql=CATEGORY_SQL), analysis(),
    )
    steps = [q for q in result.state["queries"] if q.status == "succeeded"]
    assert steps[2].filters == [{"orders.shipping_region": "South"}]


async def test_a_focus_offered_for_a_headline_figure_is_ignored(source) -> None:
    """A single overall number has no segments, so there is nothing to focus on. A model
    that offers one anyway should simply get the overall breakdown."""
    confused = analysis(
        needs_drilldown=True,
        drilldown={"dimension": "orders.shipping_region", "focus_value": "South",
                   "step_question": "How did revenue change by region?", "rationale": "x"},
    )
    result, _ = await run(
        source, headline(), confused, reply(sql=REGION_SQL), analysis(),
    )
    steps = [q for q in result.state["queries"] if q.status == "succeeded"]

    assert result.state["drilldown_depth"] == 1
    assert steps[1].filters == [], "no filter was applied to the first breakdown"


async def test_a_refusal_after_an_approved_step_does_not_restart_it(source) -> None:
    """The hazard this guards: `drill` still holds the earlier approved step, so a
    later refusal without a stop reason could route straight back into it."""
    bad = analysis(
        needs_drilldown=True,
        drilldown={"dimension": "products.category", "focus_value": "Atlantis",
                   "step_question": "Look inside Atlantis?", "rationale": "x"},
    )
    result, provider = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), bad,
    )

    assert result.state["drilldown_depth"] == 1
    assert len(provider.requests) == 4, "it must stop, not loop back into the old step"
    assert result.state["drill"] is None


async def test_a_simple_metric_question_is_never_drilled(source) -> None:
    eager = analysis(
        needs_drilldown=True,
        drilldown={"dimension": "orders.shipping_region", "focus_value": None,
                   "step_question": "By region?", "rationale": "x"},
    )
    result, provider = await run(
        source, reply(sql=HEADLINE_SQL, question_type="metric"), eager,
    )

    assert len(provider.requests) == 2
    assert result.state["drilldown_depth"] == 0


async def test_the_call_budget_stops_an_investigation(source, monkeypatch) -> None:
    from app.core.config import get_settings
    monkeypatch.setenv("MAX_LLM_CALLS_PER_ANALYSIS", "3")
    monkeypatch.setenv("VIZ_AGENT_ENABLED", "false")
    get_settings.cache_clear()

    result, provider = await run(source, headline(), by_region())

    assert len(provider.requests) == 2, "a third call would leave too little for a level"
    assert result.state["drilldown_depth"] == 0


# ── When the Analysis Agent fails ────────────────────────────────────────────
async def test_garbage_from_the_analysis_agent_falls_back_to_computed_findings(source) -> None:
    """The data and statistics exist, so the run must not fail for want of prose."""
    result, _ = await run(source, headline(), "garbage", "more garbage", "x", "y")

    state = result.state
    assert state["stop_reason"] == "answered"
    round_ = state["analysis_rounds"][-1]
    assert round_.confidence == "low"
    assert not round_.needs_drilldown, "the fallback never investigates"
    assert "21,665,720.50" in round_.summary or "Revenue moved" in round_.summary
    assert any("automatic" in c for c in round_.caveats)


async def test_the_analysis_model_being_unavailable_also_falls_back(source) -> None:
    result, _ = await run(source, headline(), ModelUnavailable("gone"))

    assert result.state["stop_reason"] == "answered"
    assert result.state["analysis_rounds"][-1].confidence == "low"


# ── The fan-out bug found in live testing ────────────────────────────────────
FANOUT_SQL = f"""
SELECT p.category AS segment,
       SUM(o.total_amount) FILTER (WHERE o.{MAY}) AS previous_value,
       SUM(o.total_amount) FILTER (WHERE o.{JUNE}) AS current_value
FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id
WHERE o.status = 'SUCCESS' AND o.shipping_region = 'South' AND o.{BOTH}
GROUP BY p.category"""


async def test_a_fan_out_breakdown_is_rejected_and_repaired(source) -> None:
    """The query below is valid, runs, and returns plausible figures that are 2.5x too
    big, because each order's total is counted once per line item. Nothing but the
    arithmetic exposes it. This is the exact failure observed with a live model."""
    result, provider = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(),
        reply(sql=FANOUT_SQL, explanation="South by category"),      # inflated
        reply(sql=CATEGORY_SQL, explanation="South by category"),    # repaired
        analysis(summary="Electronics fell."),
    )

    steps = result.state["queries"]
    assert [q.status for q in steps][-2:] == ["failed", "succeeded"]
    assert "does not add up" in steps[-2].error
    assert result.state["stop_reason"] == "answered"

    repair_prompt = provider.requests[5].messages[-1].content
    assert "PREVIOUS QUERY FAILED" in repair_prompt
    assert "line-level" in repair_prompt, "the model is told how to fix it"


async def test_an_unreconcilable_breakdown_keeps_the_earlier_findings(source) -> None:
    """If the deeper step cannot be made to add up, the run must not fail: the first
    two levels were sound and are reported, with a note on where it stopped."""
    bad = reply(sql=FANOUT_SQL)
    result, _ = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(),
        bad, bad, bad,
    )

    assert result.state["stop_reason"] == "inconclusive"
    stored = await row(result.analysis_id)
    assert stored.status == "completed", "earlier levels succeeded, so this is a result"
    assert any("could not be verified" in c for c in stored.findings["caveats"])


async def test_a_fallback_summary_says_which_slice_it_describes(source) -> None:
    """Without the scope, a summary of South alone reads as a claim about the business."""
    result, _ = await run(
        source, headline(), by_region(),
        reply(sql=REGION_SQL), by_category_in_south(),
        reply(sql=CATEGORY_SQL), ModelUnavailable("gone"),
    )
    assert "Within South" in result.state["analysis_rounds"][-1].summary
