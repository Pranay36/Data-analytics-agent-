"""A breakdown must add up to what it breaks down.

The motivating bug was real: told to hold the revenue metric fixed while drilling
into South by category, the model joined order_items and summed the order total, so
every order was counted once per line. South's May revenue of 5.96M came back as
15.2M. The query was valid and the number was plausible; only the arithmetic gave it
away.
"""

import pytest

from app.analytics import profile_result
from app.graph.reconcile import reconcile

COLS = ["segment", "previous_value", "current_value"]


def profile(rows):
    return profile_result(COLS, rows)


REGIONS = profile([
    ["North", 6_615_028.25, 6_496_449.65], ["South", 5_955_692.85, 3_856_503.45],
    ["East", 4_981_022.35, 4_630_380.70], ["West", 4_113_977.05, 4_207_952.05],
])


def test_a_breakdown_that_adds_up_passes() -> None:
    # line-level amounts: about 1.5% above order totals, because of discounts
    categories = profile([["Electronics", 4_000_000, 1_600_000], ["Fashion", 2_044_406, 2_300_000]])
    assert reconcile(categories, REGIONS, {"orders.shipping_region": "South"}).ok


def test_the_fan_out_seen_in_practice_is_caught() -> None:
    """15.2M against an expected 5.96M: the order total counted once per line item."""
    inflated = profile([["Electronics", 9_000_000, 3_000_000], ["Fashion", 6_207_963, 6_494_537]])
    verdict = reconcile(inflated, REGIONS, {"orders.shipping_region": "South"})

    assert not verdict.ok
    assert "5,955,692.85" in verdict.message, "it names the figure it should have matched"
    assert "line-level" in verdict.message, "and says how to fix it"


def test_either_period_disagreeing_is_enough() -> None:
    previous_only = profile([["A", 9_000_000, 3_856_503.45]])
    assert not reconcile(previous_only, REGIONS, {"orders.shipping_region": "South"}).ok


def test_a_breakdown_of_the_overall_total_is_checked_against_the_total() -> None:
    headline = profile_result(["previous_value", "current_value"], [[21_665_720.50, 19_191_285.85]])
    assert reconcile(REGIONS, headline, {}).ok

    wrong = profile([["North", 1, 1], ["South", 2, 2]])
    assert not reconcile(wrong, headline, {}).ok


def test_small_legitimate_differences_are_tolerated() -> None:
    """Discounts and nulls cause gaps of a few percent; those are not errors."""
    close = profile([["A", 5_955_692.85 * 1.04, 3_856_503.45 * 0.97]])
    assert reconcile(close, REGIONS, {"orders.shipping_region": "South"}).ok


def test_the_tolerance_is_configurable() -> None:
    off_by_ten = profile([["A", 5_955_692.85 * 1.10, 3_856_503.45 * 1.10]])
    focus = {"orders.shipping_region": "South"}
    assert reconcile(off_by_ten, REGIONS, focus, tolerance_pct=15).ok
    assert not reconcile(off_by_ten, REGIONS, focus, tolerance_pct=5).ok


@pytest.mark.parametrize("focus", [{"orders.shipping_region": "Atlantis"}])
def test_nothing_is_claimed_when_there_is_nothing_to_compare(focus) -> None:
    """Absent evidence of a problem, no problem is reported."""
    assert reconcile(profile([["A", 1, 1]]), REGIONS, focus).ok


def test_unfamiliar_shapes_pass_rather_than_fail() -> None:
    table = profile_result(["category", "revenue"], [["a", 1.0]])
    assert reconcile(table, REGIONS, {"orders.shipping_region": "South"}).ok
    assert reconcile(REGIONS, table, {}).ok


# ── The target is stated before the attempt, not only diagnosed after it ─────
def test_the_expected_totals_for_a_focused_segment() -> None:
    from app.graph.reconcile import expected_totals

    previous, current, label = expected_totals(
        REGIONS.comparison, {"orders.shipping_region": "South"}
    )
    assert (previous, current, label) == (5_955_692.85, 3_856_503.45, "South")


def test_the_expected_totals_without_a_focus_are_the_overall_figures() -> None:
    from app.graph.reconcile import expected_totals

    previous, current, label = expected_totals(REGIONS.comparison, {})
    assert previous == pytest.approx(21_665_720.50) and label == "the overall total"


def test_a_scale_mismatch_is_described_in_the_failure_message() -> None:
    """Seen live: the headline used monthly averages and the breakdowns summed totals, so
    three attempts failed. The message talked only about double counting."""
    headline = profile_result(["previous_value", "current_value"], [[618_956.0, 1_082_318.0]])
    totals = profile([["A", 1_000_000, 1_700_000], ["B", 1_475_824, 1_648_636]])
    verdict = reconcile(totals, headline, {})

    assert not verdict.ok
    assert "monthly averages" in verdict.message
