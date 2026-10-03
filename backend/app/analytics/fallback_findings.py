"""Findings written by code, for when the Analysis Agent cannot be used.

If the model is down, rate-limited or answering in nonsense, the *data* has still
been fetched and the statistics still computed. Throwing that away to report a
failure would be wasteful, so a plain but accurate summary is built from the
statistics instead, marked low confidence so nothing downstream mistakes it for
analysis.

It never recommends a drill-down. Investigating needs judgement, and judgement is
the thing that was unavailable.
"""

from __future__ import annotations

from app.agents.schemas import AnalysisAgentOutput, AnalysisFrame, Finding
from app.analytics import ResultProfile


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


def _pct_text(value: float | None) -> str:
    return f" ({value:+.1f}%)" if value is not None else ""


def fallback_findings(
    profile: ResultProfile | None,
    *,
    seq: int,
    frame: AnalysisFrame | None = None,
    reason: str = "automatic summary",
    filters: list[dict[str, str]] | None = None,
) -> AnalysisAgentOutput:
    metric = frame.metric_name if frame else "The measure"
    # Without this, a drill into one region reads as though it described the whole
    # business: "Revenue moved from 6.0M to 3.9M" is a very different claim about
    # South than about everything.
    scope = ""
    if filters:
        merged = {column: value for step in filters for column, value in step.items()}
        described = ", ".join(f"{value} ({column})" for column, value in merged.items())
        scope = f"Within {described}: "
    findings: list[Finding] = []
    summary = "The query ran, but a written analysis could not be produced."

    if profile is None or profile.shape == "empty":
        summary = "The query returned no data for the requested period."
    elif (c := profile.comparison) is not None:
        change = (
            f" ({c.total_pct_change:+.1f}%)" if c.total_pct_change is not None else ""
        )
        summary = (
            f"{scope}{metric} moved from {_fmt(c.total_previous)} to "
            f"{_fmt(c.total_current)}{change}."
        )
        findings.append(Finding(statement=summary, evidence_query_seq=[seq], importance="high"))

        for segment in c.segments[:3] if len(c.segments) > 1 else []:
            share = segment.share_of_total_change
            if share is None:
                continue
            findings.append(
                Finding(
                    statement=(
                        f"{segment.segment} changed by {_fmt(segment.delta)}"
                        f"{_pct_text(segment.pct_change)}, {share:.0%} of the overall change."
                    ),
                    evidence_query_seq=[seq],
                    importance="high" if segment.segment == c.dominant_segment else "medium",
                )
            )
    elif (s := profile.time_series) is not None:
        change = f" ({s.pct_change:+.1f}%)" if s.pct_change is not None else ""
        summary = (
            f"{metric} went from {_fmt(s.first.value)} in {s.first.period[:10]} to "
            f"{_fmt(s.last.value)} in {s.last.period[:10]}{change}."
        )
        findings.append(Finding(statement=summary, evidence_query_seq=[seq], importance="high"))
    elif profile.shape == "scalar" and profile.numeric:
        value = next(iter(profile.numeric.values())).total
        summary = f"The query returned a single value: {_fmt(value)}."
    else:
        summary = f"The query returned {profile.row_count} row(s)."

    return AnalysisAgentOutput(
        summary=summary,
        findings=findings[:6],
        needs_drilldown=False,
        confidence="low",
        caveats=[f"This is an {reason}, not a written analysis."],
    )
