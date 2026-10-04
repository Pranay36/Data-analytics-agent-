"""Checking that a breakdown adds back up to what it broke down.

A drill-down splits a number into parts, so the parts have to sum to the whole. When
they do not, the SQL is almost certainly wrong, and it is wrong in a way no syntax
check can see. The classic cause is a fan-out: join orders to their line items and
`SUM(orders.total_amount)` counts each order's total once per line, so a figure of
6 million quietly becomes 15 million. The query is valid, runs fine, and returns a
confident, plausible, wrong answer.

This was found in practice: the model was told to hold the metric fixed, did, and
inflated South's revenue 2.5x while breaking it down by category. Nothing else in
the pipeline noticed. A reconciliation check does, and it is a property of the
*numbers*, so it catches the whole class of bug rather than one instance.

Tolerance is deliberately loose. Legitimate differences exist (discounts mean line
amounts sum to a little more than an order total, a column may be null for some rows),
and a false alarm costs a repair attempt, whereas fan-out errors are large multiples.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics import ResultProfile
from app.analytics.profiler import ComparisonProfile


@dataclass
class Reconciliation:
    ok: bool
    message: str = ""


def expected_totals(
    parent: ComparisonProfile, focus: dict[str, str]
) -> tuple[float, float, str] | None:
    """The previous/current totals a breakdown of `focus` should add up to."""
    if not focus:
        return parent.total_previous, parent.total_current, "the overall total"

    wanted = {str(value) for value in focus.values()}
    for segment in parent.segments:
        if segment.segment in wanted:
            return segment.previous, segment.current, f"{segment.segment}"
    return None


def _off(actual: float, expected: float, tolerance: float) -> bool:
    if expected == 0:
        return actual != 0
    return abs(actual - expected) / abs(expected) > tolerance


def reconcile(
    child: ResultProfile,
    parent: ResultProfile,
    focus: dict[str, str],
    *,
    tolerance_pct: float = 15.0,
) -> Reconciliation:
    """Do the child's segments sum to the part of the parent that was drilled into?

    Returns ok when there is nothing to compare (an unfamiliar shape, or a focus that
    does not appear in the parent): absent evidence of a problem, no problem is claimed.
    """
    if child.comparison is None or parent.comparison is None:
        return Reconciliation(True)

    expected = expected_totals(parent.comparison, focus)
    if expected is None:
        return Reconciliation(True)
    expected_previous, expected_current, label = expected

    tolerance = tolerance_pct / 100
    actual_previous = child.comparison.total_previous
    actual_current = child.comparison.total_current

    if not (_off(actual_previous, expected_previous, tolerance)
            or _off(actual_current, expected_current, tolerance)):
        return Reconciliation(True)

    return Reconciliation(
        ok=False,
        message=(
            f"The breakdown does not add up to {label}. Its segments total "
            f"{actual_previous:,.2f} (previous) and {actual_current:,.2f} (current), but "
            f"{label} was {expected_previous:,.2f} and {expected_current:,.2f} in the "
            "previous step. Two common causes: values counted more than once (for example "
            "summing an order's total after joining to its line items; use the line-level "
            "amount instead), or a different scale (for example totals here where the "
            "previous step used monthly averages; apply the same division). Keep the same "
            "filters, periods and units."
        ),
    )
