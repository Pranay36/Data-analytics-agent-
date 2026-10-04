"""Dashboard validation, fallback and hydration.

The validator is the line between "the model chose what to show" and "the screen shows
it". Each test pins a way a model's spec can be wrong, and what must happen instead.
"""

from types import SimpleNamespace

import pytest

from app.agents.schemas import AnalysisAgentOutput, Finding
from app.analytics import profile_result
from app.dashboard import (
    ChartWidget,
    DashboardSpec,
    InsightCard,
    KPIWidget,
    TableWidget,
    fallback_dashboard,
    hydrate,
    validate_dashboard,
)

REGION_COLS = ["segment", "previous_value", "current_value"]
REGION_ROWS = [
    ["North", 6615028.25, 6496449.65], ["South", 5955692.85, 3856503.45],
    ["East", 4981022.35, 4630380.70], ["West", 4113977.05, 4207952.05],
]
HEADLINE_ROWS = [[21665720.5, 19191285.85]]


def query(seq, columns, rows, *, status="succeeded", dimension=None, filters=None, question="q"):
    return SimpleNamespace(
        seq=seq, status=status, columns=columns, rows=rows, row_count=len(rows),
        truncated=False, sql="SELECT 1", step_question=question, dimension=dimension,
        filters=filters or [],
        profile=profile_result(columns, rows).model_dump(mode="json") if rows else None,
    )


HEADLINE = query(1, ["previous_value", "current_value"], HEADLINE_ROWS)
REGIONS = query(2, REGION_COLS, REGION_ROWS, dimension="orders.shipping_region")
SERIES = query(3, ["month", "revenue"],
               [["2026-04-01T00:00:00", 100.0], ["2026-05-01T00:00:00", 110.0]])
QUERIES = [HEADLINE, REGIONS, SERIES]


def validate(**widgets):
    return validate_dashboard(DashboardSpec(title="t", **widgets), QUERIES)


# ── The model cannot supply a number ─────────────────────────────────────────
def test_a_widget_has_no_field_in_which_to_state_a_value() -> None:
    """The structural guarantee: there is nowhere to type a figure."""
    for model in (KPIWidget, ChartWidget, TableWidget):
        assert "value" not in model.model_fields


# ── KPIs ─────────────────────────────────────────────────────────────────────
def test_a_valid_kpi_is_kept() -> None:
    kpi = KPIWidget(label="June", query_seq=1, value_column="current_value")
    assert len(validate(kpis=[kpi]).spec.kpis) == 1


def test_a_kpi_pointing_at_a_missing_column_is_dropped_with_a_reason() -> None:
    outcome = validate(kpis=[KPIWidget(label="x", query_seq=1, value_column="revenue")])
    assert not outcome.spec.kpis
    assert any("no column 'revenue'" in d for d in outcome.dropped)


def test_a_kpi_pointing_at_a_failed_query_is_dropped() -> None:
    failed = query(9, ["a"], [[1]], status="failed")
    outcome = validate_dashboard(
        DashboardSpec(title="t", kpis=[KPIWidget(label="x", query_seq=9, value_column="a")]),
        [failed],
    )
    assert not outcome.spec.kpis and any("did not succeed" in d for d in outcome.dropped)


def test_a_non_numeric_kpi_is_dropped() -> None:
    outcome = validate(kpis=[KPIWidget(label="x", query_seq=2, value_column="segment")])
    assert not outcome.spec.kpis and any("not numeric" in d for d in outcome.dropped)


def test_column_names_are_matched_case_insensitively() -> None:
    kpi = KPIWidget(label="x", query_seq=1, value_column="CURRENT_VALUE")
    assert validate(kpis=[kpi]).spec.kpis[0].value_column == "current_value"


# ── Charts ───────────────────────────────────────────────────────────────────
def test_a_bar_chart_over_segments_is_kept() -> None:
    chart = ChartWidget(type="bar", title="t", query_seq=2, x="segment",
                        y=["previous_value", "current_value"])
    assert validate(charts=[chart]).spec.charts[0].type == "bar"


def test_a_chart_plotting_a_text_column_is_dropped() -> None:
    chart = ChartWidget(type="bar", title="t", query_seq=2, x="segment", y=["segment"])
    outcome = validate(charts=[chart])
    assert not outcome.spec.charts and any("numeric" in d for d in outcome.dropped)


def test_a_line_over_categories_is_drawn_as_a_bar() -> None:
    """A line across regions would imply a trend that does not exist."""
    chart = ChartWidget(type="line", title="t", query_seq=2, x="segment", y=["current_value"])
    outcome = validate(charts=[chart])
    assert outcome.spec.charts[0].type == "bar"
    assert any("needs a time axis" in d for d in outcome.dropped)


def test_a_line_over_time_stays_a_line() -> None:
    chart = ChartWidget(type="line", title="t", query_seq=3, x="month", y=["revenue"])
    assert validate(charts=[chart]).spec.charts[0].type == "line"


def test_a_pie_with_too_many_slices_becomes_a_bar() -> None:
    many = query(5, ["k", "v"], [[f"k{i}", float(i)] for i in range(10)])
    spec = DashboardSpec(title="t", charts=[
        ChartWidget(type="pie", title="t", query_seq=5, x="k", y=["v"])])
    assert validate_dashboard(spec, [many]).spec.charts[0].type == "bar"


def test_a_pie_over_few_categories_is_kept() -> None:
    chart = ChartWidget(type="pie", title="t", query_seq=2, x="segment", y=["current_value"])
    assert validate(charts=[chart]).spec.charts[0].type == "pie"


def test_invalid_y_columns_are_trimmed_not_fatal() -> None:
    chart = ChartWidget(type="bar", title="t", query_seq=2, x="segment",
                        y=["current_value", "ghost"])
    assert validate(charts=[chart]).spec.charts[0].y == ["current_value"]


# ── One bad widget never costs the dashboard ─────────────────────────────────
def test_good_widgets_survive_alongside_a_bad_one() -> None:
    good = KPIWidget(label="ok", query_seq=1, value_column="current_value")
    bad = KPIWidget(label="bad", query_seq=1, value_column="nope")
    outcome = validate(kpis=[bad, good])
    assert [k.label for k in outcome.spec.kpis] == ["ok"]


def test_oversized_lists_are_truncated_not_rejected() -> None:
    """A weaker model returning six KPIs should get four, not a failed run."""
    kpis = [KPIWidget(label=f"k{i}", query_seq=1, value_column="current_value") for i in range(6)]
    assert len(validate(kpis=kpis).spec.kpis) == 4


# ── Insights ─────────────────────────────────────────────────────────────────
def test_an_insight_quoting_a_real_figure_is_kept() -> None:
    card = InsightCard(title="South", text="South fell by 2,099,189.40 (-35.2%).")
    assert len(validate(insights=[card]).spec.insights) == 1


def test_an_insight_with_an_invented_figure_is_dropped() -> None:
    """The dashboard must not state a number the data does not contain."""
    card = InsightCard(title="South", text="South fell by 3,400,000 in June.")
    outcome = validate(insights=[card])
    assert not outcome.spec.insights and any("not in the data" in d for d in outcome.dropped)


def test_evidence_pointing_nowhere_is_removed() -> None:
    card = InsightCard(title="x", text="South fell 35.2%.", evidence_query_seq=[2, 99])
    assert validate(insights=[card]).spec.insights[0].evidence_query_seq == [2]


# ── Always inspectable ───────────────────────────────────────────────────────
def test_a_table_of_the_underlying_data_is_always_present() -> None:
    kpi = KPIWidget(label="ok", query_seq=1, value_column="current_value")
    outcome = validate(kpis=[kpi])
    assert outcome.spec.tables and outcome.spec.tables[0].query_seq == 3


def test_table_columns_are_filtered_to_those_that_exist() -> None:
    table = TableWidget(title="t", query_seq=2, columns=["segment", "ghost"])
    assert validate(tables=[table]).spec.tables[0].columns == ["segment"]


def test_ids_are_assigned_when_the_model_omits_them() -> None:
    spec = DashboardSpec(title="t", kpis=[KPIWidget(label="a", query_seq=1, value_column="x")])
    assert spec.kpis[0].id == "kpi_1"


# ── Fallback ─────────────────────────────────────────────────────────────────
def test_the_fallback_shows_a_headline_as_kpis_with_a_change() -> None:
    spec = fallback_dashboard("Why did revenue fall?", [HEADLINE])
    assert [k.value_column for k in spec.kpis] == ["current_value", "previous_value"]
    assert spec.kpis[0].comparison_column == "previous_value"


def test_the_fallback_charts_a_breakdown_as_grouped_bars() -> None:
    spec = fallback_dashboard("q", [REGIONS])
    assert spec.charts[0].type == "bar"
    assert spec.charts[0].y == ["previous_value", "current_value"]
    assert "shipping_region" in spec.charts[0].title


def test_the_fallback_charts_a_time_series_as_a_line() -> None:
    assert fallback_dashboard("q", [SERIES]).charts[0].type == "line"


def test_the_fallback_builds_a_valid_dashboard_from_anything() -> None:
    """Whatever shape came back, validating the fallback must lose nothing."""
    spec = fallback_dashboard("q", QUERIES)
    outcome = validate_dashboard(spec, QUERIES)
    assert not [d for d in outcome.dropped if "dropped" in d or "did not" in d]
    assert outcome.spec.kpis and outcome.spec.charts and outcome.spec.tables


def test_the_fallback_turns_findings_into_insights() -> None:
    analysis = AnalysisAgentOutput(
        summary="Revenue fell.", findings=[
            Finding(statement="South fell by 2,099,189.40.", evidence_query_seq=[2]),
            Finding(statement="West rose by 93,975.00.", evidence_query_seq=[2]),
        ])
    insights = fallback_dashboard("q", [REGIONS], analysis).insights
    assert [i.severity for i in insights] == ["negative", "positive"]


def test_the_fallback_survives_an_empty_run() -> None:
    assert fallback_dashboard("q", []).is_empty


# ── Hydration ────────────────────────────────────────────────────────────────
def test_kpi_values_are_read_from_the_data_not_from_the_spec() -> None:
    spec = fallback_dashboard("q", [HEADLINE])
    payload = hydrate(spec, [HEADLINE])

    current = payload["kpis"][0]
    assert current["value"] == 19191285.85
    assert current["comparison_value"] == 21665720.5
    assert current["delta_pct"] == pytest.approx(-11.42, abs=0.01)


def test_only_referenced_datasets_are_included_and_carry_their_sql() -> None:
    spec = DashboardSpec(title="t", tables=[TableWidget(title="t", query_seq=2)])
    payload = hydrate(spec, QUERIES)

    assert list(payload["datasets"]) == ["2"]
    assert payload["datasets"]["2"]["sql"] == "SELECT 1"
    assert payload["datasets"]["2"]["columns"][1] == {"name": "previous_value", "type": "number"}


def test_a_change_from_zero_has_no_percentage() -> None:
    zero = query(1, ["previous_value", "current_value"], [[0, 50]])
    payload = hydrate(fallback_dashboard("q", [zero]), [zero])
    assert payload["kpis"][0]["delta_pct"] is None
