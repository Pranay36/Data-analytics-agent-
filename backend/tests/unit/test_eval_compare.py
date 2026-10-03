"""The comparison must forgive presentation and punish wrong values."""

import pytest

from app.evaluation.compare import compare_results


def ok(gold, actual, mode, **kw):
    return compare_results(gold, actual, mode, **kw).ok


# ── scalar ───────────────────────────────────────────────────────────────────
def test_a_scalar_matches_regardless_of_the_column_it_is_in() -> None:
    assert ok([[19191285.85]], [["June", 19191285.85]], "scalar")


def test_a_wrong_scalar_fails_and_says_why() -> None:
    result = compare_results([[100.0]], [[250.0]], "scalar")
    assert not result.ok and "100" in result.detail and "250" in result.detail


def test_rounding_differences_are_not_errors() -> None:
    assert ok([[19191285.85]], [[19191285.9]], "scalar")
    assert ok([[19191285.85]], [[19191286]], "scalar")


def test_the_tolerance_is_enforced() -> None:
    assert not ok([[100.0]], [[102.0]], "scalar", tolerance=0.005)
    assert ok([[100.0]], [[102.0]], "scalar", tolerance=0.05)


def test_a_zero_gold_value_is_compared_absolutely() -> None:
    assert ok([[0]], [[0]], "scalar")
    assert not ok([[0]], [[5]], "scalar")


def test_the_unfiltered_total_is_not_accepted_for_the_filtered_one() -> None:
    """The headline trap: ignoring status inflates revenue by about 8%."""
    assert not ok([[251_928_207.60]], [[272_169_980.0]], "scalar")


def test_a_rate_may_be_a_fraction_or_a_percentage() -> None:
    assert ok([[15.2]], [[0.152]], "scalar", allow_percent_scale=True)
    assert not ok([[15.2]], [[0.152]], "scalar", allow_percent_scale=False)


# ── rows ─────────────────────────────────────────────────────────────────────
REGIONS = [["North", 100.0], ["South", 50.0], ["East", 75.0]]


def test_unordered_rows_match_in_any_order() -> None:
    assert ok(REGIONS, [["East", 75.0], ["North", 100.0], ["South", 50.0]], "rows_unordered")


def test_column_order_does_not_matter() -> None:
    assert ok(REGIONS, [[100.0, "North"], [50.0, "South"], [75.0, "East"]], "rows_unordered")


def test_labels_are_case_insensitive() -> None:
    assert ok(REGIONS, [["north", 100.0], ["SOUTH", 50.0], ["East", 75.0]], "rows_unordered")


def test_an_extra_helpful_column_is_accepted() -> None:
    assert ok(REGIONS, [["North", 100.0, 0.4], ["South", 50.0, 0.2], ["East", 75.0, 0.3]],
              "rows_unordered")


def test_a_wrong_value_in_one_row_fails() -> None:
    assert not ok(REGIONS, [["North", 100.0], ["South", 99.0], ["East", 75.0]], "rows_unordered")


def test_a_wrong_row_count_fails() -> None:
    assert not ok(REGIONS, REGIONS[:2], "rows_unordered")
    assert not ok(REGIONS, [*REGIONS, ["West", 1.0]], "rows_unordered")


def test_one_repeated_cell_cannot_satisfy_two_values() -> None:
    """Two gold values must be matched by two distinct cells."""
    assert not ok([[5.0, 5.0]], [[5.0, "x"]], "rows_unordered")


def test_ordered_rows_must_be_in_order() -> None:
    assert ok(REGIONS, REGIONS, "rows_ordered")
    assert not ok(REGIONS, [REGIONS[1], REGIONS[0], REGIONS[2]], "rows_ordered")


def test_a_month_bucket_matches_with_or_without_a_time() -> None:
    gold = [["2026-05-01T00:00:00", 10.0], ["2026-06-01T00:00:00", 20.0]]
    assert ok(gold, [["2026-05-01", 10.0], ["2026-06-01", 20.0]], "rows_ordered")


# ── top-k ────────────────────────────────────────────────────────────────────
def test_top_k_compares_the_leading_labels_in_order() -> None:
    gold = [["Ana", 9], ["Bo", 8], ["Cy", 7]]
    assert ok(gold, [["Ana", 9.1], ["Bo", 8.2], ["Cy", 7.3]], "top_k", top_k=3)


def test_top_k_fails_when_the_order_is_wrong() -> None:
    gold = [["Ana", 9], ["Bo", 8], ["Cy", 7]]
    assert not ok(gold, [["Bo", 9], ["Ana", 8], ["Cy", 7]], "top_k", top_k=3)


def test_top_k_only_looks_at_the_first_k() -> None:
    gold = [["Ana", 9], ["Bo", 8]]
    assert ok(gold, [["Ana", 9], ["Bo", 8], ["Zed", 1]], "top_k", top_k=2)


# ── degenerate cases ─────────────────────────────────────────────────────────
def test_an_empty_answer_never_matches() -> None:
    assert not ok(REGIONS, [], "rows_unordered")


def test_an_empty_gold_result_is_reported_as_a_broken_case() -> None:
    result = compare_results([], [["x"]], "scalar")
    assert not result.ok and "gold" in result.detail


def test_an_unknown_mode_is_an_error() -> None:
    with pytest.raises(ValueError):
        compare_results([[1]], [[1]], "vibes")
