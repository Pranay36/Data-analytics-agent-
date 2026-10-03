"""Deciding whether two query results mean the same thing.

The system's SQL will rarely match the gold SQL character for character, and should
not have to: `SUM(total_amount) AS revenue` and `SUM(total_amount) AS total_revenue`
are the same answer. So comparison looks at *values*, never at column names, and is
tolerant of column order, extra columns and rounding. What it is strict about is the
numbers and labels themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass
class Comparison:
    ok: bool
    detail: str = ""


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _normalise(value: Any) -> Any:
    """Make equivalent presentations of one value compare equal."""
    if isinstance(value, str):
        text = value.strip()
        # 2026-06-01T00:00:00 and 2026-06-01 are the same month bucket.
        if match := _DATE_PREFIX.match(text):
            return match.group(1)
        return text.lower()
    return value


def _numbers_match(actual: float, expected: float, tolerance: float, percent_scale: bool) -> bool:
    def close(a: float, b: float) -> bool:
        if b == 0:
            return abs(a) <= tolerance
        return abs(a - b) / abs(b) <= tolerance

    if close(actual, expected):
        return True
    # A rate is right whether it is written 0.152 or 15.2.
    return percent_scale and (close(actual * 100, expected) or close(actual, expected * 100))


def _cells_match(a: Any, b: Any, tolerance: float, percent_scale: bool) -> bool:
    if _is_number(a) and _is_number(b):
        return _numbers_match(float(a), float(b), tolerance, percent_scale)
    return _normalise(a) == _normalise(b)


def _row_contains(
    actual: list[Any], gold: list[Any], tolerance: float, percent_scale: bool
) -> bool:
    """Does `actual` hold every value in `gold`, in any column order?

    A superset is accepted: returning an extra helpful column is not an error. The
    values are matched one-to-one, so a gold row cannot be satisfied by one repeated cell.
    """
    remaining = list(actual)
    for expected in gold:
        for index, candidate in enumerate(remaining):
            if _cells_match(candidate, expected, tolerance, percent_scale):
                del remaining[index]
                break
        else:
            return False
    return True


def compare_results(
    gold: list[list[Any]],
    actual: list[list[Any]],
    mode: str,
    *,
    tolerance: float = 0.005,
    allow_percent_scale: bool = False,
    top_k: int | None = None,
) -> Comparison:
    """Compare `actual` with `gold`.

    Modes:
        scalar          one number, found anywhere in the result
        rows_unordered  the same set of rows, in any order
        rows_ordered    the same rows in the same order
        top_k           the same first `top_k` labels, in order
    """
    if not gold:
        return Comparison(False, "the gold query returned no rows, so the case is broken")
    if not actual:
        return Comparison(False, "the answer returned no rows")

    if mode == "scalar":
        expected = next((c for c in gold[0] if _is_number(c)), gold[0][0])
        for row in actual:
            for cell in row:
                if _cells_match(cell, expected, tolerance, allow_percent_scale):
                    return Comparison(True)
        return Comparison(False, f"expected {expected!r}, got {actual[0]!r}")

    if mode == "top_k":
        count = top_k or len(gold)
        labels = [
            _normalise(next((c for c in row if isinstance(c, str)), row[0])) for row in gold[:count]
        ]
        got = [
            _normalise(next((c for c in row if isinstance(c, str)), row[0]))
            for row in actual[:count]
        ]
        if got == labels:
            return Comparison(True)
        missing = [label for label in labels if label not in got]
        return Comparison(False, f"top {count} differs; missing {missing[:3]}")

    if len(actual) != len(gold):
        return Comparison(False, f"expected {len(gold)} rows, got {len(actual)}")

    if mode == "rows_ordered":
        for position, (g, a) in enumerate(zip(gold, actual, strict=True)):
            if not _row_contains(a, g, tolerance, allow_percent_scale):
                return Comparison(False, f"row {position + 1}: expected {g!r}, got {a!r}")
        return Comparison(True)

    if mode == "rows_unordered":
        unmatched = list(actual)
        for g in gold:
            for index, candidate in enumerate(unmatched):
                if _row_contains(candidate, g, tolerance, allow_percent_scale):
                    del unmatched[index]
                    break
            else:
                return Comparison(False, f"no row matches {g!r}")
        return Comparison(True)

    raise ValueError(f"Unknown compare mode {mode!r}")
