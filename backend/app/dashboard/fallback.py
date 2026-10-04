"""A dashboard built by rules, with no model involved.

Used when the Visualization Agent is switched off (to save free-tier quota while
developing), unavailable, or returns something that fails validation. The aim is a
dashboard that is plain but correct, never a failed run: the data was fetched and the
statistics computed, so there is always enough to show.

The rules are the ones a person would follow:
    one overall figure           -> KPI cards (with the change)
    segments, before and after   -> grouped bar chart
    values over time             -> line chart
    a handful of categories      -> bar chart
    anything else                -> a table
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.schemas import AnalysisAgentOutput, AnalysisFrame
from app.dashboard.spec import (
    MAX_CHARTS,
    MAX_INSIGHTS,
    MAX_KPIS,
    MAX_TABLES,
    ChartWidget,
    DashboardSpec,
    InsightCard,
    KPIWidget,
    TableWidget,
)
from app.dashboard.validator import column_kind

_NEGATIVE = re.compile(r"\b(fell|fall|drop|dropped|declin\w+|decreas\w+|lower|down)\b", re.I)
_POSITIVE = re.compile(r"\b(rose|rise|increas\w+|grew|growth|up|higher)\b", re.I)
MAX_BAR_CATEGORIES = 30


def _severity(text: str) -> str:
    if _NEGATIVE.search(text):
        return "negative"
    if _POSITIVE.search(text):
        return "positive"
    return "info"


def _label(frame: AnalysisFrame | None, which: str, default: str) -> str:
    if frame is None:
        return default
    period = frame.current_period if which == "current" else frame.comparison_period
    return f"{period.label} {frame.metric_name.lower()}" if period else default


def fallback_dashboard(
    question: str,
    queries: list[Any],
    analysis: AnalysisAgentOutput | None = None,
    frame: AnalysisFrame | None = None,
) -> DashboardSpec:
    kpis: list[KPIWidget] = []
    charts: list[ChartWidget] = []
    tables: list[TableWidget] = []

    succeeded = [q for q in queries if q.status == "succeeded" and q.rows]
    for query in succeeded:
        profile = query.profile or {}
        shape = profile.get("shape")
        columns = query.columns

        if shape == "comparison" and "previous_value" in columns:
            segments = len(query.rows)
            if segments == 1:
                kpis += [
                    KPIWidget(
                        label=_label(frame, "current", "Current"),
                        query_seq=query.seq,
                        value_column="current_value",
                        format="compact",
                        comparison_column="previous_value",
                    ),
                    KPIWidget(
                        label=_label(frame, "previous", "Previous"),
                        query_seq=query.seq,
                        value_column="previous_value",
                        format="compact",
                    ),
                ]
            else:
                x = "segment" if "segment" in columns else columns[0]
                merged = {c: v for step in query.filters for c, v in step.items()}
                where = f" within {', '.join(merged.values())}" if merged else ""
                by = f" by {query.dimension.split('.')[-1]}" if query.dimension else ""
                charts.append(
                    ChartWidget(
                        type="bar",
                        title=f"Change{by}{where}",
                        query_seq=query.seq,
                        x=x,
                        y=["previous_value", "current_value"],
                        format="compact",
                    )
                )

        elif shape == "time_series":
            numeric = next(
                (
                    c
                    for i, c in enumerate(columns)
                    if i > 0 and column_kind(query.rows, i) == "number"
                ),
                None,
            )
            if numeric:
                charts.append(
                    ChartWidget(
                        type="line",
                        title=query.step_question[:70],
                        query_seq=query.seq,
                        x=columns[0],
                        y=[numeric],
                        format="compact",
                    )
                )

        elif shape == "scalar":
            kpis.append(
                KPIWidget(
                    label=question[:60],
                    query_seq=query.seq,
                    value_column=columns[0],
                    format="compact",
                )
            )

        elif shape == "table" and len(query.rows) <= MAX_BAR_CATEGORIES:
            numeric = [c for i, c in enumerate(columns) if column_kind(query.rows, i) == "number"]
            label = next(
                (c for i, c in enumerate(columns) if column_kind(query.rows, i) == "text"), None
            )
            if label and numeric:
                charts.append(
                    ChartWidget(
                        type="bar",
                        title=query.step_question[:70],
                        query_seq=query.seq,
                        x=label,
                        y=numeric[:1],
                        format="compact",
                    )
                )

    if succeeded:
        tables.append(TableWidget(title="Result data", query_seq=succeeded[-1].seq))

    insights: list[InsightCard] = []
    if analysis is not None:
        for finding in analysis.findings[:MAX_INSIGHTS]:
            insights.append(
                InsightCard(
                    title="Finding",
                    text=finding.statement,
                    severity=_severity(finding.statement),  # type: ignore[arg-type]
                    evidence_query_seq=finding.evidence_query_seq,
                )
            )

    return DashboardSpec(
        title=question.rstrip("?")[:100],
        summary=analysis.summary if analysis else "",
        kpis=kpis[:MAX_KPIS],
        charts=charts[:MAX_CHARTS],
        tables=tables[:MAX_TABLES],
        insights=insights,
    )
