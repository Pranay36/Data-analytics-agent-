"""The dashboard specification.

The model never writes UI code or chooses a number. It chooses *what to show*: which
executed query feeds which widget, which chart type fits, how to title and describe
it. Every widget points at a query by `query_seq`, and its values are read from that
query's result when the dashboard is built. That is the property that matters: a
dashboard cannot display a figure the data does not contain, because there is no
field in which a model could type one.

The same models serve as the model's output schema and as what the API stores, so a
spec is validated the same way wherever it came from. Lists are deliberately not given
hard maximum lengths: a weaker model that returns six KPIs should get the first four,
not a failed run, so the limits are applied by `validator` instead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

NumberFormat = Literal["number", "currency", "percent", "compact"]
ChartType = Literal["line", "bar", "area", "pie"]
Severity = Literal["info", "positive", "warning", "negative"]

MAX_KPIS = 4
MAX_CHARTS = 4
MAX_TABLES = 2
MAX_INSIGHTS = 5


class KPIWidget(BaseModel):
    id: str = ""
    label: str = Field(description="e.g. 'June 2026 revenue'.")
    query_seq: int = Field(description="Number of the query that holds the value.")
    value_column: str = Field(description="Column of that query's result holding the value.")
    row_index: int = 0
    format: NumberFormat = "number"
    comparison_column: str | None = Field(
        default=None,
        description="Optional column holding the earlier value, so a change can be shown.",
    )


class ChartWidget(BaseModel):
    id: str = ""
    type: ChartType
    title: str
    query_seq: int
    x: str = Field(description="Column for the horizontal axis or the categories.")
    y: list[str] = Field(description="One to three numeric columns.")
    series: str | None = None
    format: NumberFormat = "number"
    description: str | None = Field(default=None, description="One-line observation.")


class TableWidget(BaseModel):
    id: str = ""
    title: str
    query_seq: int
    columns: list[str] | None = None


class InsightCard(BaseModel):
    id: str = ""
    title: str
    text: str = Field(
        description="A finding in plain language. Quote only numbers present in the data."
    )
    severity: Severity = "info"
    evidence_query_seq: list[int] = Field(default_factory=list)


class DashboardSpec(BaseModel):
    title: str
    summary: str = ""
    kpis: list[KPIWidget] = Field(default_factory=list)
    charts: list[ChartWidget] = Field(default_factory=list)
    tables: list[TableWidget] = Field(default_factory=list)
    insights: list[InsightCard] = Field(default_factory=list)

    @model_validator(mode="after")
    def _assign_ids(self) -> DashboardSpec:
        """Ids are for the frontend's keys; a model should not have to invent them."""
        for prefix, widgets in (
            ("kpi", self.kpis),
            ("chart", self.charts),
            ("table", self.tables),
            ("insight", self.insights),
        ):
            for index, widget in enumerate(widgets, start=1):
                if not widget.id:
                    widget.id = f"{prefix}_{index}"
        return self

    @property
    def is_empty(self) -> bool:
        return not (self.kpis or self.charts or self.tables)
