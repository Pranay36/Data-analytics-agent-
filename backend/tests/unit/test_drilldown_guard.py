"""The rules that stop the investigation loop. Each is enforced in code, never by prompt."""

from datetime import date

import pytest

from app.agents.schemas import AnalysisFrame, DrillDownRequest, Period
from app.graph.drilldown import DimensionInfo, DrillContext, check_drilldown

FRAME = AnalysisFrame(
    metric_name="Revenue", metric_sql="SUM(total_amount)", base_table="orders",
    current_period=Period(start=date(2026, 6, 1), end=date(2026, 7, 1), label="June 2026"),
    comparison_period=Period(start=date(2026, 5, 1), end=date(2026, 6, 1), label="May 2026"),
)
DIMENSIONS = [
    DimensionInfo(table="orders", column="shipping_region",
                  sample_values=["North", "South", "East", "West"]),
    DimensionInfo(table="products", column="category",
                  sample_values=["Electronics", "Fashion", "Books"]),
    DimensionInfo(table="orders", column="channel", sample_values=["web", "mobile_app"]),
]


def context(**overrides) -> DrillContext:
    base = dict(
        question_type="root_cause", frame=FRAME, depth=0, max_depth=3,
        remaining_calls=10, remaining_tokens=60_000, used_dimensions=[], filter_path=[],
        previous_step_questions=["Why did revenue fall?"], dimensions=DIMENSIONS,
        last_segments=["North", "South", "East", "West"],
    )
    return DrillContext(**{**base, **overrides})


def request(**overrides) -> DrillDownRequest:
    base = dict(
        dimension="products.category",
        focus_filter={"orders.shipping_region": "South"},
        step_question="How did revenue change by category within South, May vs June?",
        rationale="South dominates the decline.",
    )
    return DrillDownRequest(**{**base, **overrides})


def test_a_valid_request_is_approved() -> None:
    assert check_drilldown(request(), context()).approved


# ── Limits ───────────────────────────────────────────────────────────────────
def test_depth_is_capped() -> None:
    verdict = check_drilldown(request(), context(depth=3, max_depth=3))
    assert not verdict.approved
    assert verdict.stop_reason == "max_depth"
    assert verdict.note, "the user is told why the investigation stopped"


def test_the_last_allowed_level_is_still_permitted() -> None:
    assert check_drilldown(request(), context(depth=2, max_depth=3)).approved


def test_the_call_budget_is_respected() -> None:
    verdict = check_drilldown(request(), context(remaining_calls=1))
    assert not verdict.approved
    assert verdict.stop_reason == "budget_exhausted"


def test_calls_held_in_reserve_are_not_spent_on_drilling() -> None:
    """The dashboard step must still be able to run after the last drill."""
    assert not check_drilldown(request(), context(remaining_calls=3, reserve_calls=2)).approved
    assert check_drilldown(request(), context(remaining_calls=4, reserve_calls=2)).approved


def test_the_token_budget_is_respected() -> None:
    verdict = check_drilldown(request(), context(remaining_tokens=3_000))
    assert not verdict.approved and verdict.stop_reason == "budget_exhausted"


# ── What may be drilled ──────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["metric", "trend", "top_n", "breakdown", None])
def test_only_comparisons_are_investigated(kind) -> None:
    assert not check_drilldown(request(), context(question_type=kind)).approved


@pytest.mark.parametrize("kind", ["comparison", "root_cause"])
def test_comparisons_and_root_causes_are(kind) -> None:
    assert check_drilldown(request(), context(question_type=kind)).approved


def test_it_needs_both_periods_to_hold_the_comparison_fixed() -> None:
    no_comparison = FRAME.model_copy(update={"comparison_period": None})
    assert not check_drilldown(request(), context(frame=no_comparison)).approved
    assert not check_drilldown(request(), context(frame=None)).approved


# ── The request itself ───────────────────────────────────────────────────────
def test_an_invented_dimension_is_refused() -> None:
    assert not check_drilldown(request(dimension="products.color"), context()).approved


def test_a_dimension_cannot_be_reused() -> None:
    assert not check_drilldown(request(), context(used_dimensions=["products.category"])).approved


def test_breaking_down_by_a_column_already_filtered_is_refused() -> None:
    """Grouping by the column being filtered returns one row and explains nothing."""
    same = request(dimension="orders.shipping_region",
                   focus_filter={"orders.shipping_region": "South"})
    assert not check_drilldown(same, context()).approved


def test_a_dimension_filtered_earlier_on_the_path_is_refused() -> None:
    earlier = context(filter_path=[{"products.category": "Electronics"}])
    assert not check_drilldown(request(dimension="products.category"), earlier).approved


def test_an_invented_segment_value_is_refused() -> None:
    """An invented value would run, return nothing, and waste a whole level."""
    bad = request(focus_filter={"orders.shipping_region": "Atlantis"})
    verdict = check_drilldown(bad, context())
    assert not verdict.approved and "Atlantis" in verdict.reason


def test_a_value_known_from_the_catalog_is_accepted_even_if_not_in_the_last_result() -> None:
    assert check_drilldown(request(), context(last_segments=[])).approved


def test_a_value_seen_in_the_last_result_is_accepted() -> None:
    other = request(focus_filter={"orders.channel": "web"})
    assert check_drilldown(other, context(last_segments=["web"])).approved


def test_an_empty_focus_is_refused_when_there_are_segments_to_choose_from() -> None:
    assert not check_drilldown(request(focus_filter={}), context()).approved


def test_the_first_drilldown_may_break_down_a_headline_figure_with_no_focus() -> None:
    """A why-question opens with one overall number, so there is no segment yet.
    Without this the loop could never start."""
    headline = context(last_was_headline=True, last_segments=["total"])
    assert check_drilldown(request(focus_filter={}), headline).approved


def test_filtering_on_an_unknown_column_is_refused() -> None:
    assert not check_drilldown(request(focus_filter={"orders.mystery": "x"}), context()).approved


def test_a_repeated_question_is_refused_even_if_worded_slightly_differently() -> None:
    repeated = context(previous_step_questions=[
        "How did revenue change by category within South, May vs June?"])
    differently = request(
        step_question="how did revenue change by category within south May vs June"
    )
    assert not check_drilldown(differently, repeated).approved


# ── The guarantee ────────────────────────────────────────────────────────────
def test_no_sequence_of_requests_can_exceed_the_depth_limit() -> None:
    """Whatever the model asks for, the loop ends. The property the whole design rests on.

    Plenty of valid, unused dimensions are supplied on purpose, so that the depth cap
    is the only thing that can stop it. With too few, the loop would run out of
    dimensions first and the test would pass without exercising the cap at all.
    """
    many = [DimensionInfo(table="t", column=f"c{i}", sample_values=["v"]) for i in range(20)]
    used: list[str] = []
    depth = 0

    for index in range(50):  # a model that never stops asking
        dim = many[index % len(many)]
        ask = request(
            dimension=dim.name, focus_filter={"t.c19": "v"}, step_question=f"question {index}"
        )
        verdict = check_drilldown(
            ask, context(depth=depth, max_depth=3, used_dimensions=list(used), dimensions=many)
        )
        if verdict.approved:
            depth += 1
            used.append(dim.name)

    assert depth == 3, "it must reach the cap, and never pass it"


# ── A flat breakdown on one dimension says nothing about the others ──────────
def test_after_a_breakdown_with_no_dominant_segment_another_dimension_needs_no_focus() -> None:
    """Found by evaluation: refunds broken down by reason showed no standout, and the
    investigation concluded 'broad-based' without ever trying product category, where the
    driver was. There is nothing to focus on, so an empty focus must be allowed."""
    flat = context(last_had_no_dominant=True)
    assert check_drilldown(request(focus_filter={}), flat).approved


def test_an_empty_focus_is_still_refused_when_a_segment_did_stand_out() -> None:
    assert not check_drilldown(request(focus_filter={}), context()).approved


def test_trying_the_same_dimension_again_is_still_refused() -> None:
    flat = context(last_had_no_dominant=True, used_dimensions=["products.category"])
    assert not check_drilldown(request(focus_filter={}), flat).approved
