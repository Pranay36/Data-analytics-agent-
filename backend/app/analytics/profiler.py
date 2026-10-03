"""Facts about a query result, computed by code.

Language models are unreliable at arithmetic. Asked "which region explains most of
the decline?" from a table of numbers, they will often name a plausible one without
having done the sum. So the numbers an analysis rests on — percentage changes,
each segment's share of the total change, which segment dominates — are computed
here, exactly, and handed to the model as facts to interpret rather than sums to
perform.

The drill-down loop depends on one convention, the *comparison contract*: a result
with columns `previous_value` and `current_value`, optionally with a `segment`
column. The Query Agent is told to produce that shape for comparison questions, and
this module knows how to read it. A fixed shape is what makes "how much of the drop
does South explain?" computable regardless of how the SQL was written.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

Shape = Literal["empty", "comparison", "time_series", "scalar", "table"]

# Thresholds for calling one segment *the* explanation. Deliberately conservative:
# a drill-down into a segment that does not really dominate sends the investigation
# down a blind alley, which is worse than stopping a level early.
DOMINANT_SHARE = 0.50
DOMINANT_SHARE_IF_CLEAR_LEAD = 0.35
CLEAR_LEAD_RATIO = 1.5

_ISO = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2})?)?")


class SegmentChange(BaseModel):
    segment: str
    previous: float
    current: float
    delta: float
    pct_change: float | None
    """None when the previous value is zero: a percentage of nothing is undefined."""
    share_of_total_change: float | None
    """This segment's delta as a fraction of the overall delta. 0.85 means it
    accounts for 85% of the change. Negative when it moved against the total."""


class ComparisonProfile(BaseModel):
    total_previous: float
    total_current: float
    total_delta: float
    total_pct_change: float | None
    is_material: bool
    segments: list[SegmentChange] = Field(default_factory=list)
    """Ordered with the biggest contributor to the overall change first."""
    dominant_segment: str | None = None


class SeriesPoint(BaseModel):
    period: str
    value: float


class TimeSeriesProfile(BaseModel):
    first: SeriesPoint
    last: SeriesPoint
    pct_change: float | None
    peak: SeriesPoint
    trough: SeriesPoint
    largest_move: SeriesPoint | None = None
    """The period with the biggest change from the one before it."""
    largest_move_pct: float | None = None


class NumericSummary(BaseModel):
    minimum: float
    maximum: float
    total: float
    mean: float


class ResultProfile(BaseModel):
    shape: Shape
    row_count: int
    numeric: dict[str, NumericSummary] = Field(default_factory=dict)
    comparison: ComparisonProfile | None = None
    time_series: TimeSeriesProfile | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _pct(previous: float, current: float) -> float | None:
    return None if previous == 0 else (current - previous) / abs(previous) * 100


def is_empty(rows: list[list]) -> bool:
    """No rows, or only NULLs.

    An aggregate over nothing returns one row of NULL rather than zero rows.
    """
    return not rows or all(all(value is None for value in row) for row in rows)


def _numeric_summaries(columns: list[str], rows: list[list]) -> dict[str, NumericSummary]:
    out: dict[str, NumericSummary] = {}
    for index, name in enumerate(columns):
        values = [n for row in rows if (n := _number(row[index])) is not None]
        if values and len(values) == len([r for r in rows if r[index] is not None]):
            out[name] = NumericSummary(
                minimum=min(values), maximum=max(values),
                total=sum(values), mean=sum(values) / len(values),
            )
    return out


def _profile_comparison(
    columns: list[str], rows: list[list], materiality_pct: float
) -> ComparisonProfile:
    previous_at = columns.index("previous_value")
    current_at = columns.index("current_value")
    segment_at = columns.index("segment") if "segment" in columns else None

    entries = []
    for row in rows:
        previous = _number(row[previous_at]) or 0.0
        current = _number(row[current_at]) or 0.0
        name = str(row[segment_at]) if segment_at is not None else "total"
        entries.append((name, previous, current))

    total_previous = sum(p for _, p, _ in entries)
    total_current = sum(c for _, _, c in entries)
    total_delta = total_current - total_previous
    total_pct = _pct(total_previous, total_current)

    segments = [
        SegmentChange(
            segment=name, previous=previous, current=current, delta=current - previous,
            pct_change=_pct(previous, current),
            share_of_total_change=(
                (current - previous) / total_delta if total_delta != 0 else None
            ),
        )
        for name, previous, current in entries
    ]

    # Largest contributor *in the direction of the overall change* first. For a
    # decline that is the biggest fall, which is what a "why" question wants.
    direction = -1.0 if total_delta < 0 else 1.0
    segments.sort(key=lambda s: -(s.delta * direction))

    dominant = None
    if len(segments) > 1 and total_delta != 0:
        top = segments[0].share_of_total_change or 0.0
        runner_up = segments[1].share_of_total_change or 0.0
        if top >= DOMINANT_SHARE or (
            top >= DOMINANT_SHARE_IF_CLEAR_LEAD and top >= runner_up * CLEAR_LEAD_RATIO
        ):
            dominant = segments[0].segment

    return ComparisonProfile(
        total_previous=total_previous,
        total_current=total_current,
        total_delta=total_delta,
        total_pct_change=total_pct,
        is_material=total_pct is not None and abs(total_pct) >= materiality_pct,
        segments=segments,
        dominant_segment=dominant,
    )


def _looks_temporal(values: list[Any]) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and all(isinstance(v, str) and _ISO.match(v) for v in present)


def _profile_series(columns: list[str], rows: list[list]) -> TimeSeriesProfile | None:
    if len(columns) < 2 or not _looks_temporal([row[0] for row in rows]):
        return None

    value_at = next(
        (i for i in range(1, len(columns)) if all(_number(r[i]) is not None for r in rows)),
        None,
    )
    if value_at is None or len(rows) < 2:
        return None

    points = sorted(
        (SeriesPoint(period=str(row[0]), value=float(row[value_at])) for row in rows),
        key=lambda p: p.period,
    )

    largest, largest_pct = None, None
    for before, after in zip(points, points[1:], strict=False):
        change = _pct(before.value, after.value)
        if change is not None and (largest_pct is None or abs(change) > abs(largest_pct)):
            largest, largest_pct = after, change

    return TimeSeriesProfile(
        first=points[0],
        last=points[-1],
        pct_change=_pct(points[0].value, points[-1].value),
        peak=max(points, key=lambda p: p.value),
        trough=min(points, key=lambda p: p.value),
        largest_move=largest,
        largest_move_pct=largest_pct,
    )


def profile_result(
    columns: list[str], rows: list[list], *, materiality_pct: float = 5.0
) -> ResultProfile:
    """Describe a result set. Never raises: an unfamiliar shape just gets less detail."""
    if is_empty(rows):
        return ResultProfile(shape="empty", row_count=len(rows))

    numeric = _numeric_summaries(columns, rows)

    if "previous_value" in columns and "current_value" in columns:
        return ResultProfile(
            shape="comparison",
            row_count=len(rows),
            numeric=numeric,
            comparison=_profile_comparison(columns, rows, materiality_pct),
        )

    if (series := _profile_series(columns, rows)) is not None:
        return ResultProfile(
            shape="time_series", row_count=len(rows), numeric=numeric, time_series=series
        )

    shape: Shape = "scalar" if len(rows) == 1 and len(columns) == 1 else "table"
    return ResultProfile(shape=shape, row_count=len(rows), numeric=numeric)


def render_profile(profile: ResultProfile) -> str:
    """The profile as compact text for a prompt."""
    if profile.shape == "empty":
        return "No data."

    lines: list[str] = []
    if (comparison := profile.comparison) is not None:
        pct = comparison.total_pct_change
        lines.append(
            f"Overall: {comparison.total_previous:,.2f} -> {comparison.total_current:,.2f} "
            f"({comparison.total_delta:+,.2f}"
            + (f", {pct:+.1f}%" if pct is not None else "")
            + f"). Material change: {'yes' if comparison.is_material else 'no'}."
        )
        if len(comparison.segments) > 1:
            lines.append("Segments, biggest contributor first:")
            for seg in comparison.segments:
                share = seg.share_of_total_change
                lines.append(
                    f"  - {seg.segment}: {seg.previous:,.2f} -> {seg.current:,.2f} "
                    f"({seg.delta:+,.2f}"
                    + (f", {seg.pct_change:+.1f}%" if seg.pct_change is not None else "")
                    + (f", {share:.0%} of the overall change" if share is not None else "")
                    + ")"
                )
            lines.append(
                f"Dominant segment: {comparison.dominant_segment}"
                if comparison.dominant_segment
                else "Dominant segment: none (the change is spread across segments)."
            )
    elif (series := profile.time_series) is not None:
        lines.append(
            f"Series {series.first.period} to {series.last.period}: "
            f"{series.first.value:,.2f} -> {series.last.value:,.2f}"
            + (f" ({series.pct_change:+.1f}%)" if series.pct_change is not None else "")
            + f". Peak {series.peak.period} ({series.peak.value:,.2f}), "
            f"trough {series.trough.period} ({series.trough.value:,.2f})."
        )
        if series.largest_move and series.largest_move_pct is not None:
            lines.append(
                f"Largest single move: {series.largest_move.period} "
                f"({series.largest_move_pct:+.1f}% on the prior period)."
            )
    else:
        for name, summary in list(profile.numeric.items())[:4]:
            lines.append(
                f"{name}: total {summary.total:,.2f}, mean {summary.mean:,.2f}, "
                f"range {summary.minimum:,.2f} to {summary.maximum:,.2f}"
            )
    return "\n".join(lines) or f"{profile.row_count} row(s)."


__all__ = [
    "ComparisonProfile", "ResultProfile", "SegmentChange", "TimeSeriesProfile",
    "is_empty", "profile_result", "render_profile",
]
