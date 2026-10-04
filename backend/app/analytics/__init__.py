"""Deterministic analysis of query results."""

from app.analytics.grounding import Grounding, check_grounding, extract_numbers
from app.analytics.profiler import (
    ComparisonProfile,
    ResultProfile,
    SegmentChange,
    TimeSeriesProfile,
    is_empty,
    profile_result,
    render_profile,
)

__all__ = [
    "ComparisonProfile",
    "Grounding",
    "check_grounding",
    "extract_numbers",
    "ResultProfile",
    "SegmentChange",
    "TimeSeriesProfile",
    "is_empty",
    "profile_result",
    "render_profile",
]
