"""Dashboards end to end: the agent proposes, the validator checks, the result is stored.

Real database and real queries; only the model is scripted. The point of these tests is
that a dashboard is *always* produced from data that exists, whatever the model does.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.dashboard import DashboardSpec, hydrate
from app.db.models import Dashboard
from app.db.session import get_sessionmaker
from app.llm.errors import ModelUnavailable
from app.services.analysis_runner import run_analysis

from .conftest import EMBEDDINGS, analysis, reply, scripted_llm
from .test_drilldown_workflow import (
    CATEGORY_SQL,
    FRAME,
    HEADLINE_SQL,
    REGION_SQL,
    by_category_in_south,
    by_region,
)

pytestmark = pytest.mark.integration

QUESTION = "Why did revenue fall in June 2026?"


def headline() -> str:
    return reply(sql=HEADLINE_SQL, question_type="root_cause", frame=FRAME)


def dashboard_reply(**fields) -> str:
    base = {"title": "Revenue fell in June", "summary": "South drove it.",
            "kpis": [], "charts": [], "tables": [], "insights": []}
    return json.dumps({**base, **fields})


@pytest.fixture
def viz_on(monkeypatch):
    monkeypatch.setenv("VIZ_AGENT_ENABLED", "true")
    get_settings.cache_clear()


async def run(source, *replies):
    client, provider = scripted_llm(*replies)
    result = await run_analysis(source, QUESTION, llm=client, embeddings=EMBEDDINGS)
    return result, provider


async def stored(analysis_id: str) -> Dashboard | None:
    async with get_sessionmaker()() as session:
        return await session.scalar(select(Dashboard).where(Dashboard.analysis_id == analysis_id))


GOOD = dashboard_reply(
    kpis=[{"label": "June revenue", "query_seq": 1, "value_column": "current_value",
           "format": "compact", "comparison_column": "previous_value"}],
    charts=[{"type": "bar", "title": "Revenue by region", "query_seq": 2, "x": "segment",
             "y": ["previous_value", "current_value"]}],
    insights=[{"title": "South", "text": "South fell by 2,099,189.40 (-35.2%).",
               "severity": "negative", "evidence_query_seq": [2]}],
)


# ── The model's dashboard ────────────────────────────────────────────────────
async def test_a_valid_model_dashboard_is_validated_and_stored(source, viz_on) -> None:
    result, _ = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis(summary="South."), GOOD
    )
    state = result.state

    assert state["dashboard_source"] == "llm"
    assert [c.type for c in state["dashboard"].charts] == ["bar"]

    row = await stored(result.analysis_id)
    assert row is not None and row.generated_by == "llm"
    assert DashboardSpec.model_validate(row.spec).kpis[0].label == "June revenue"


async def test_widgets_that_do_not_hold_up_are_dropped_and_the_rest_kept(source, viz_on) -> None:
    mixed = dashboard_reply(
        kpis=[
            {"label": "ok", "query_seq": 1, "value_column": "current_value"},
            {"label": "invented", "query_seq": 1, "value_column": "ghost_column"},
            {"label": "no such query", "query_seq": 42, "value_column": "current_value"},
        ],
        charts=[{"type": "bar", "title": "ok", "query_seq": 2, "x": "segment",
                 "y": ["current_value"]}],
    )
    result, _ = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis(), mixed
    )
    state = result.state

    assert [k.label for k in state["dashboard"].kpis] == ["ok"]
    assert len(state["dashboard_dropped"]) == 2
    assert state["dashboard_source"] == "llm"


async def test_an_insight_with_an_invented_figure_never_reaches_the_dashboard(
    source, viz_on
) -> None:
    bad = dashboard_reply(
        charts=[{"type": "bar", "title": "t", "query_seq": 2, "x": "segment",
                 "y": ["current_value"]}],
        insights=[{"title": "South", "text": "South lost 9,999,999 in June."}],
    )
    result, _ = await run(source, headline(), by_region(), reply(sql=REGION_SQL), analysis(), bad)

    assert result.state["dashboard"].insights == []
    assert any("not in the data" in d for d in result.state["dashboard_dropped"])


async def test_the_dashboard_agent_is_shown_data_shapes_not_the_data(source, viz_on) -> None:
    _, provider = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis(), GOOD
    )
    prompt = provider.requests[-1].messages[-1].content

    assert "Columns: segment (text), previous_value (number), current_value (number)" in prompt
    assert "Sample:" in prompt


# ── When the model cannot be used ────────────────────────────────────────────
async def test_a_garbage_reply_falls_back_to_a_rule_built_dashboard(source, viz_on) -> None:
    result, _ = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis(),
        "garbage", "more", "x", "y",
    )
    state = result.state

    assert state["dashboard_source"] == "fallback"
    assert state["dashboard"].charts, "the fallback charts the breakdown"
    assert (await stored(result.analysis_id)).generated_by == "fallback"


async def test_an_unavailable_model_falls_back_too(source, viz_on) -> None:
    result, _ = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis(),
        ModelUnavailable("gone"),
    )
    assert result.state["dashboard_source"] == "fallback"


async def test_a_draft_with_no_surviving_widgets_is_replaced_by_the_fallback(
    source, viz_on
) -> None:
    hollow = dashboard_reply(
        kpis=[{"label": "x", "query_seq": 1, "value_column": "ghost"}],
        charts=[{"type": "bar", "title": "x", "query_seq": 2, "x": "nope", "y": ["nada"]}],
    )
    result, _ = await run(source, headline(), by_region(), reply(sql=REGION_SQL), analysis(), hollow)

    assert result.state["dashboard_source"] == "fallback"
    assert result.state["dashboard"].charts


async def test_with_the_agent_off_no_call_is_spent_and_a_dashboard_still_exists(source) -> None:
    result, provider = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL), analysis()
    )

    assert len(provider.requests) == 4, "two queries and two analyses, no dashboard call"
    assert result.state["dashboard_source"] == "fallback"
    assert (await stored(result.analysis_id)) is not None


# ── What a dashboard is built from ───────────────────────────────────────────
async def test_a_full_investigation_is_shown_level_by_level(source) -> None:
    result, _ = await run(
        source, headline(), by_region(), reply(sql=REGION_SQL),
        by_category_in_south(), reply(sql=CATEGORY_SQL), analysis(),
    )
    spec = result.state["dashboard"]

    assert [k.value_column for k in spec.kpis] == ["current_value", "previous_value"]
    titles = [c.title for c in spec.charts]
    assert any("shipping_region" in t for t in titles)
    assert any("category" in t and "South" in t for t in titles), titles


async def test_kpi_values_come_from_the_query_not_the_model(source) -> None:
    """The headline figure on screen is the database's, to the paisa."""
    result, _ = await run(source, headline(), by_region(), reply(sql=REGION_SQL), analysis())
    payload = hydrate(result.state["dashboard"], result.state["queries"])

    current = payload["kpis"][0]
    assert current["value"] == pytest.approx(19_191_285.85)
    assert current["delta_pct"] == pytest.approx(-11.42, abs=0.01)


async def test_the_frontend_payload_carries_the_sql_behind_each_widget(source) -> None:
    result, _ = await run(source, headline(), by_region(), reply(sql=REGION_SQL), analysis())
    payload = hydrate(result.state["dashboard"], result.state["queries"])

    assert all(d["sql"] for d in payload["datasets"].values())
    assert all("LIMIT" in d["sql"] for d in payload["datasets"].values())


# ── No data, no dashboard ────────────────────────────────────────────────────
async def test_a_refusal_produces_no_dashboard(source) -> None:
    refusal = reply(can_answer=False, cannot_answer_reason="No such data.", sql=None)
    result, _ = await run(source, refusal)

    assert result.state.get("dashboard") is None
    assert await stored(result.analysis_id) is None
