"""Turning a spec into what the frontend renders: the spec plus the data it refers to.

The frontend never queries a database and never sees SQL it could run. It receives the
spec and a `datasets` map, and looks each widget's data up by `query_seq`. KPI values
are resolved here, on the server, from the stored result rows.
"""

from __future__ import annotations

from typing import Any

from app.dashboard.spec import DashboardSpec, KPIWidget
from app.dashboard.validator import column_kind


def _kpi_value(kpi: KPIWidget, queries: dict[int, Any]) -> dict[str, Any]:
    query = queries[kpi.query_seq]
    row = query.rows[kpi.row_index]
    value = row[query.columns.index(kpi.value_column)]

    resolved: dict[str, Any] = {**kpi.model_dump(), "value": value}
    if kpi.comparison_column:
        earlier = row[query.columns.index(kpi.comparison_column)]
        resolved["comparison_value"] = earlier
        # A change from zero is undefined, not infinite.
        resolved["delta_pct"] = None if not earlier else (value - earlier) / abs(earlier) * 100
    return resolved


def hydrate(spec: DashboardSpec, queries: list[Any]) -> dict[str, Any]:
    """Build the API payload: the spec, resolved KPIs, and the datasets they use."""
    available = {q.seq: q for q in queries if q.status == "succeeded"}

    referenced: set[int] = set()
    for widget in [*spec.kpis, *spec.charts, *spec.tables]:
        referenced.add(widget.query_seq)
    for card in spec.insights:
        referenced.update(card.evidence_query_seq)

    datasets = {}
    for seq in sorted(referenced & available.keys()):
        query = available[seq]
        datasets[str(seq)] = {
            "columns": [
                {"name": name, "type": column_kind(query.rows, index)}
                for index, name in enumerate(query.columns)
            ],
            "rows": query.rows,
            "row_count": query.row_count,
            "truncated": query.truncated,
            "sql": query.sql,
            "step_question": query.step_question,
        }

    return {
        "spec": spec.model_dump(),
        "kpis": [_kpi_value(kpi, available) for kpi in spec.kpis if kpi.query_seq in available],
        "datasets": datasets,
    }
