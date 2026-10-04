"""Checking a dashboard spec against the data it claims to show.

A spec is a set of claims: "this column of that query is a number worth a KPI card",
"these two columns make a bar chart". Each is checked against the real result, and a
widget that does not hold up is dropped with a reason rather than rendered broken. One
bad widget never costs the whole dashboard.

The checks that matter most are the ones a model cannot be trusted with:
  - a widget must point at a query that ran and succeeded
  - every column it names must exist, and a plotted value must actually be numeric
  - a line chart needs a time axis, and a pie chart a handful of slices
  - an insight may only quote numbers that appear in the data
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.analytics.grounding import check_grounding
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

Kind = Literal["number", "temporal", "text", "empty"]
MAX_PIE_SLICES = 6
_ISO = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2}(:\d{2})?)?")


@dataclass
class Outcome:
    spec: DashboardSpec
    dropped: list[str] = field(default_factory=list)
    """Why each widget was dropped or changed, for the debug panel and for evaluation."""


def column_kind(rows: list[list[Any]], index: int) -> Kind:
    """What a column holds, judged from its values: the connectors do not report types."""
    values = [row[index] for row in rows if index < len(row) and row[index] is not None]
    if not values:
        return "empty"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
        return "number"
    if all(isinstance(v, str) and _ISO.match(v) for v in values):
        return "temporal"
    return "text"


def _succeeded(queries: list[Any]) -> dict[int, Any]:
    return {q.seq: q for q in queries if q.status == "succeeded"}


def _position(columns: list[str], name: str) -> int | None:
    lowered = [c.lower() for c in columns]
    return lowered.index(name.lower()) if name.lower() in lowered else None


def _canonical(columns: list[str], name: str) -> str | None:
    index = _position(columns, name)
    return columns[index] if index is not None else None


def _check_kpi(kpi: KPIWidget, queries: dict[int, Any]) -> tuple[KPIWidget | None, str | None]:
    query = queries.get(kpi.query_seq)
    if query is None:
        return None, f"KPI {kpi.label!r}: query {kpi.query_seq} did not succeed"

    column = _canonical(query.columns, kpi.value_column)
    if column is None:
        return None, f"KPI {kpi.label!r}: no column {kpi.value_column!r}"

    index = query.columns.index(column)
    if kpi.row_index >= len(query.rows):
        return None, f"KPI {kpi.label!r}: row {kpi.row_index} does not exist"
    if column_kind([query.rows[kpi.row_index]], index) != "number":
        return None, f"KPI {kpi.label!r}: {column!r} is not numeric"

    comparison = None
    if kpi.comparison_column:
        candidate = _canonical(query.columns, kpi.comparison_column)
        if (
            candidate
            and column_kind([query.rows[kpi.row_index]], query.columns.index(candidate)) == "number"
        ):
            comparison = candidate
    return kpi.model_copy(update={"value_column": column, "comparison_column": comparison}), None


def _check_chart(
    chart: ChartWidget, queries: dict[int, Any]
) -> tuple[ChartWidget | None, list[str]]:
    notes: list[str] = []
    query = queries.get(chart.query_seq)
    if query is None:
        return None, [f"chart {chart.title!r}: query {chart.query_seq} did not succeed"]

    x = _canonical(query.columns, chart.x)
    if x is None:
        return None, [f"chart {chart.title!r}: no column {chart.x!r}"]

    ys = [c for name in chart.y if (c := _canonical(query.columns, name)) and c != x]
    ys = [c for c in ys if column_kind(query.rows, query.columns.index(c)) == "number"]
    if not ys:
        return None, [f"chart {chart.title!r}: none of {chart.y} is a numeric column"]
    ys = ys[:3]

    series = _canonical(query.columns, chart.series) if chart.series else None
    chart_type = chart.type
    x_kind = column_kind(query.rows, query.columns.index(x))

    # A line over unordered categories implies a trend that is not there.
    if chart_type in ("line", "area") and x_kind != "temporal":
        notes.append(f"chart {chart.title!r}: {chart_type} needs a time axis, drawn as bar")
        chart_type = "bar"
    if chart_type == "pie" and (len(query.rows) > MAX_PIE_SLICES or len(ys) != 1):
        notes.append(
            f"chart {chart.title!r}: pie needs one value and at most "
            f"{MAX_PIE_SLICES} slices, drawn as bar"
        )
        chart_type = "bar"

    return chart.model_copy(update={"x": x, "y": ys, "series": series, "type": chart_type}), notes


def _check_table(
    table: TableWidget, queries: dict[int, Any]
) -> tuple[TableWidget | None, str | None]:
    query = queries.get(table.query_seq)
    if query is None:
        return None, f"table {table.title!r}: query {table.query_seq} did not succeed"
    wanted = [c for name in (table.columns or []) if (c := _canonical(query.columns, name))]
    return table.model_copy(update={"columns": wanted or None}), None


def _check_insight(
    card: InsightCard, queries: dict[int, Any], all_queries: list[Any]
) -> tuple[InsightCard | None, str | None]:
    evidence = [seq for seq in card.evidence_query_seq if seq in queries]
    grounding = check_grounding(f"{card.title}. {card.text}", all_queries)
    if not grounding.ok:
        # The dashboard must not state a figure the data does not contain, whatever
        # the model believed it was rounding.
        return (
            None,
            f"insight {card.title!r}: quotes numbers not in the data {grounding.ungrounded[:3]}",
        )
    return card.model_copy(update={"evidence_query_seq": evidence}), None


def validate_dashboard(draft: DashboardSpec, queries: list[Any]) -> Outcome:
    """Keep what holds up, drop what does not, and say why."""
    available = _succeeded(queries)
    dropped: list[str] = []

    kpis: list[KPIWidget] = []
    for kpi in draft.kpis:
        checked, reason = _check_kpi(kpi, available)
        if checked:
            kpis.append(checked)
        else:
            dropped.append(reason or "KPI dropped")

    charts: list[ChartWidget] = []
    for chart in draft.charts:
        checked, notes = _check_chart(chart, available)
        dropped += notes
        if checked:
            charts.append(checked)

    tables: list[TableWidget] = []
    for table in draft.tables:
        checked, reason = _check_table(table, available)
        if checked:
            tables.append(checked)
        else:
            dropped.append(reason or "table dropped")

    insights: list[InsightCard] = []
    for card in draft.insights:
        checked, reason = _check_insight(card, available, queries)
        if checked:
            insights.append(checked)
        else:
            dropped.append(reason or "insight dropped")

    for name, kept, limit in (
        ("KPIs", kpis, MAX_KPIS),
        ("charts", charts, MAX_CHARTS),
        ("tables", tables, MAX_TABLES),
        ("insights", insights, MAX_INSIGHTS),
    ):
        if len(kept) > limit:
            dropped.append(f"{len(kept) - limit} {name} beyond the limit of {limit}")

    # Always show the underlying data: a dashboard whose numbers cannot be inspected is
    # asking to be taken on trust.
    if not tables and available:
        last = available[max(available)]
        tables = [TableWidget(title="Result data", query_seq=last.seq)]

    spec = DashboardSpec(
        title=draft.title,
        summary=draft.summary,
        kpis=kpis[:MAX_KPIS],
        charts=charts[:MAX_CHARTS],
        tables=tables[:MAX_TABLES],
        insights=insights[:MAX_INSIGHTS],
    )
    return Outcome(spec=spec, dropped=dropped)
