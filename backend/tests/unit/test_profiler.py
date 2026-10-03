"""The numbers an analysis rests on must be exact, because a model will not check them."""

import pytest

from app.analytics import is_empty, profile_result, render_profile

COLS = ["segment", "previous_value", "current_value"]

# The planted June 2026 pattern, from the demo dataset's ground truth.
REGIONS = [
    ["North", 6615028.25, 6496449.65],
    ["South", 5955692.85, 3856503.45],
    ["East", 4981022.35, 4630380.70],
    ["West", 4113977.05, 4207952.05],
]


def comparison(rows, cols=COLS):
    return profile_result(cols, rows).comparison


def test_overall_change_is_computed_from_the_segments() -> None:
    c = comparison(REGIONS)
    assert c.total_previous == pytest.approx(21_665_720.50)
    assert c.total_current == pytest.approx(19_191_285.85)
    assert c.total_pct_change == pytest.approx(-11.42, abs=0.01)
    assert c.is_material


def test_the_biggest_contributor_to_a_decline_comes_first() -> None:
    c = comparison(REGIONS)
    assert [s.segment for s in c.segments][0] == "South"
    assert c.segments[-1].segment == "West", "the one that grew goes last"


def test_a_segments_share_of_the_total_change() -> None:
    south = comparison(REGIONS).segments[0]
    assert south.pct_change == pytest.approx(-35.24, abs=0.01)
    assert south.share_of_total_change == pytest.approx(0.848, abs=0.005)


def test_shares_sum_to_one() -> None:
    shares = [s.share_of_total_change for s in comparison(REGIONS).segments]
    assert sum(shares) == pytest.approx(1.0)


def test_a_segment_that_moved_against_the_total_has_a_negative_share() -> None:
    west = next(s for s in comparison(REGIONS).segments if s.segment == "West")
    assert west.share_of_total_change < 0


def test_a_dominant_segment_is_identified() -> None:
    assert comparison(REGIONS).dominant_segment == "South"


def test_a_spread_out_decline_has_no_dominant_segment() -> None:
    """Drilling into one of four equal contributors would be a blind alley."""
    even = [["A", 100, 90], ["B", 100, 90], ["C", 100, 90], ["D", 100, 90]]
    assert comparison(even).dominant_segment is None


def test_a_clear_lead_below_half_still_counts() -> None:
    """40% of the drop against a runner-up of 25%: not a majority, but a clear lead."""
    rows = [["A", 100, 60], ["B", 100, 75], ["C", 100, 80], ["D", 100, 85]]  # -40 -25 -20 -15
    c = comparison(rows)
    assert c.segments[0].share_of_total_change == pytest.approx(0.40)
    assert c.dominant_segment == "A"


def test_a_narrow_lead_below_half_does_not() -> None:
    """38% against 30%: ahead, but not by enough to be *the* explanation."""
    rows = [["A", 100, 62], ["B", 100, 70], ["C", 100, 83], ["D", 100, 85]]  # -38 -30 -17 -15
    c = comparison(rows)
    assert c.segments[0].share_of_total_change == pytest.approx(0.38)
    assert c.dominant_segment is None


def test_a_majority_is_dominant_whatever_the_runner_up() -> None:
    rows = [["A", 100, 60], ["B", 100, 85], ["C", 100, 88], ["D", 100, 90]]  # A is 40 of 77
    assert comparison(rows).dominant_segment == "A"


def test_materiality_follows_the_threshold() -> None:
    small = profile_result(COLS, [["A", 100, 98], ["B", 100, 99]], materiality_pct=5.0)
    assert not small.comparison.is_material
    assert profile_result(COLS, [["A", 100, 90]], materiality_pct=5.0).comparison.is_material


def test_a_headline_row_without_a_segment_column() -> None:
    """Step zero of a why-question: one row, previous and current."""
    profile = profile_result(["previous_value", "current_value"], [[48.1e6, 39.4e6]])
    c = profile.comparison
    assert [s.segment for s in c.segments] == ["total"]
    assert c.total_pct_change == pytest.approx(-18.09, abs=0.01)
    assert c.dominant_segment is None, "there is nothing to be dominant among"


def test_a_segment_with_no_previous_value_has_no_percentage() -> None:
    """A percentage of zero is undefined, not infinite."""
    rows = [["New", 0, 50], ["Old", 100, 100]]
    new = next(s for s in comparison(rows).segments if s.segment == "New")
    assert new.pct_change is None
    assert new.delta == 50


def test_null_values_count_as_zero() -> None:
    rows = [["A", None, 10], ["B", 20, None]]
    c = comparison(rows)
    assert c.total_previous == 20 and c.total_current == 10


def test_no_change_at_all_does_not_divide_by_zero() -> None:
    c = comparison([["A", 100, 100], ["B", 50, 50]])
    assert c.total_delta == 0
    assert all(s.share_of_total_change is None for s in c.segments)
    assert c.dominant_segment is None


# ── Other shapes ─────────────────────────────────────────────────────────────
def test_empty_results() -> None:
    assert profile_result(["x"], []).shape == "empty"
    assert profile_result(["revenue"], [[None]]).shape == "empty"
    assert is_empty([[None, None]])
    assert not is_empty([[0]]), "zero is data"


def test_a_time_series() -> None:
    rows = [["2026-04-01T00:00:00", 100.0], ["2026-05-01T00:00:00", 110.0],
            ["2026-06-01T00:00:00", 70.0]]
    series = profile_result(["month", "revenue"], rows).time_series

    assert series.first.value == 100 and series.last.value == 70
    assert series.pct_change == pytest.approx(-30.0)
    assert series.peak.period.startswith("2026-05")
    assert series.largest_move.period.startswith("2026-06")
    assert series.largest_move_pct == pytest.approx(-36.36, abs=0.01)


def test_a_series_is_ordered_regardless_of_row_order() -> None:
    rows = [["2026-06-01", 70.0], ["2026-04-01", 100.0], ["2026-05-01", 110.0]]
    assert profile_result(["month", "v"], rows).time_series.first.period == "2026-04-01"


def test_a_scalar() -> None:
    assert profile_result(["revenue"], [[19191285.85]]).shape == "scalar"


def test_an_unfamiliar_table_is_summarised_not_rejected() -> None:
    rows = [["Electronics", 135e6], ["Fashion", 46.8e6]]
    profile = profile_result(["category", "revenue"], rows)

    assert profile.shape == "table"
    assert profile.numeric["revenue"].total == pytest.approx(181.8e6)


def test_text_columns_are_not_summarised_as_numbers() -> None:
    assert "category" not in profile_result(["category", "n"], [["a", 1], ["b", 2]]).numeric


# ── Rendering ────────────────────────────────────────────────────────────────
def test_rendering_states_the_facts_a_model_needs() -> None:
    text = render_profile(profile_result(COLS, REGIONS))

    assert "-11.4%" in text
    assert "South" in text and "85% of the overall change" in text
    assert "Dominant segment: South" in text


def test_rendering_says_when_nothing_dominates() -> None:
    even = [["A", 100, 90], ["B", 100, 90], ["C", 100, 90], ["D", 100, 90]]
    assert "none" in render_profile(profile_result(COLS, even)).lower()
