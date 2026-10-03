"""The workflow end to end, with the model scripted.

Each test pins one decision the graph makes: what it does when the model is right,
when it is wrong in a repairable way, when it refuses, and when something outside
its control fails. The real database, guard and connector are used; only the model
is replaced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Analysis, AnalysisQuery
from app.db.session import get_sessionmaker
from app.services.analysis_runner import run_analysis

from .conftest import EMBEDDINGS, REVENUE_SQL, analysis, reply, scripted_llm

pytestmark = pytest.mark.integration


async def run(source, question, *replies, **kwargs):
    client, provider = scripted_llm(*replies)
    result = await run_analysis(source, question, llm=client, embeddings=EMBEDDINGS, **kwargs)
    return result, provider


async def stored_queries(analysis_id: str):
    async with get_sessionmaker()() as session:
        return (await session.scalars(
            select(AnalysisQuery).where(AnalysisQuery.analysis_id == analysis_id)
            .order_by(AnalysisQuery.seq))).all()


# ── Success ──────────────────────────────────────────────────────────────────
async def test_a_correct_query_costs_one_query_call_and_one_analysis_call(source) -> None:
    result, provider = await run(
        source, "What was revenue in May 2026?",
        reply(sql=REVENUE_SQL, question_type="metric", tables_used=["orders"]),
        analysis(),
    )

    state = result.state
    assert state["stop_reason"] == "answered"
    assert result.llm_calls == 2
    assert len(provider.requests) == 2

    query = state["queries"][-1]
    assert query.status == "succeeded"
    assert query.row_count == 1
    assert float(query.rows[0][0]) > 0


async def test_the_executed_sql_is_the_guards_rewrite_with_a_row_cap(source) -> None:
    result, _ = await run(source, "revenue in May", reply(sql=REVENUE_SQL), analysis())

    executed = result.state["queries"][-1]
    assert "LIMIT 500" in executed.sql, "the model's SQL must not run unmodified"
    assert "LIMIT" not in executed.original_sql


async def test_the_run_is_recorded(source) -> None:
    result, _ = await run(
        source, "revenue in May", reply(sql=REVENUE_SQL, question_type="metric"), analysis()
    )

    async with get_sessionmaker()() as session:
        row = await session.get(Analysis, result.analysis_id)

    assert row.status == "completed"
    assert row.stop_reason == "answered"
    assert row.question_type == "metric"
    assert row.retrieved_context["chunks"], "what retrieval supplied must be inspectable"

    rows = await stored_queries(result.analysis_id)
    assert [r.status for r in rows] == ["succeeded"]
    assert rows[0].result_preview is not None


async def test_the_first_query_fixes_the_question_type_and_frame(source) -> None:
    frame = {
        "metric_name": "Revenue", "metric_sql": "SUM(total_amount)", "base_table": "orders",
        "current_period": {"start": "2026-06-01", "end": "2026-07-01", "label": "June"},
        "comparison_period": {"start": "2026-05-01", "end": "2026-06-01", "label": "May"},
    }
    result, _ = await run(
        source, "revenue in May",
        reply(sql=REVENUE_SQL, question_type="comparison", frame=frame),
        analysis(),
    )
    assert result.state["question_type"] == "comparison"
    assert result.state["frame"].metric_name == "Revenue"


# ── Repair ───────────────────────────────────────────────────────────────────
async def test_a_rejected_query_is_repaired_using_the_specific_reason(source) -> None:
    result, provider = await run(
        source, "revenue in May",
        reply(sql="SELECT o.region FROM orders o"),   # a column that does not exist
        reply(sql=REVENUE_SQL),
        analysis(),
    )

    assert result.state["stop_reason"] == "answered"
    assert len(provider.requests) == 3

    repair_prompt = provider.requests[1].messages[-1].content
    assert "PREVIOUS QUERY FAILED" in repair_prompt
    assert "does not exist" in repair_prompt, "the model must be told what was wrong"

    rows = await stored_queries(result.analysis_id)
    assert [(r.attempt, r.status) for r in rows] == [(1, "rejected"), (2, "succeeded")]


async def test_a_dangerous_query_never_reaches_the_database(source) -> None:
    result, _ = await run(
        source, "revenue",
        reply(sql="SELECT 1; DROP TABLE orders"),
        reply(sql=REVENUE_SQL),
        analysis(),
    )
    rows = await stored_queries(result.analysis_id)
    assert rows[0].status == "rejected"
    assert rows[0].sql is None, "a rejected query must never be recorded as executed"


async def test_a_database_error_is_repaired(source) -> None:
    """Passes the guard (valid structure, real columns) but fails when run."""
    result, _ = await run(
        source, "revenue",
        reply(sql="SELECT CAST(status AS integer) AS bad FROM orders"),
        reply(sql=REVENUE_SQL),
        analysis(),
    )
    rows = await stored_queries(result.analysis_id)
    assert [r.status for r in rows] == ["failed", "succeeded"]
    assert result.state["stop_reason"] == "answered"


async def test_zero_rows_triggers_exactly_one_retry_with_hints(source) -> None:
    wrong_literal = REVENUE_SQL.replace("'SUCCESS'", "'completed'")
    result, provider = await run(
        source, "revenue in May",
        reply(sql=wrong_literal),
        reply(sql=REVENUE_SQL),
        analysis(),
    )

    assert result.state["stop_reason"] == "answered"
    assert "no data" in provider.requests[1].messages[-1].content
    assert result.state["queries"][-1].row_count == 1


async def test_an_aggregate_over_nothing_counts_as_empty(source) -> None:
    """SUM over no rows returns ONE row of NULL, not zero rows. A row-count check
    alone would accept it as an answer."""
    wrong = REVENUE_SQL.replace("'SUCCESS'", "'completed'")
    result, _ = await run(
        source, "revenue", reply(sql=wrong), reply(sql=REVENUE_SQL), analysis()
    )

    first = result.state["queries"][0]
    assert first.row_count == 1 and first.rows[0][0] is None, "premise: one NULL row"
    assert len(result.state["queries"]) == 2, "it must still be retried"


async def test_a_second_empty_result_is_accepted_as_the_answer(source) -> None:
    """'No data for that period' is a legitimate result, not a failure."""
    empty = REVENUE_SQL.replace("2026-05-01", "2030-05-01").replace("2026-06-01", "2030-06-01")
    result, provider = await run(
        source, "revenue in 2030", reply(sql=empty), reply(sql=empty), analysis()
    )

    assert result.state["stop_reason"] == "answered"
    assert result.state["queries"][-1].rows == [[None]], "an empty aggregate is one NULL row"
    assert len(provider.requests) == 3, "one retry, then accept, then analyse"


# ── Bounded ──────────────────────────────────────────────────────────────────
async def test_repairs_are_bounded(source) -> None:
    """Three tries (one plus two repairs), then give up — never loop."""
    bad = reply(sql="SELECT nonexistent_thing FROM orders o WHERE o.nope = 1")
    result, provider = await run(source, "revenue", bad, bad, bad)

    assert len(provider.requests) == 3
    assert result.state["stop_reason"] == "invalid_sql"
    assert result.state["error"].code == "INVALID_SQL"

    async with get_sessionmaker()() as session:
        row = await session.get(Analysis, result.analysis_id)
    assert row.status == "failed"


# ── Refusal and failure ──────────────────────────────────────────────────────
async def test_a_refusal_completes_rather_than_fails(source) -> None:
    """"This data cannot answer that" is a correct reply, not an error."""
    result, _ = await run(
        source, "What is our influencer ROI?",
        reply(can_answer=False, cannot_answer_reason="No influencer data exists.", sql=None),
    )

    assert result.state["stop_reason"] == "cannot_answer"
    assert result.state["queries"] == [], "nothing may be executed after a refusal"

    async with get_sessionmaker()() as session:
        row = await session.get(Analysis, result.analysis_id)
    assert row.status == "completed"


async def test_all_models_failing_is_reported_not_raised(source) -> None:
    from app.llm.errors import ModelUnavailable

    result, _ = await run(source, "revenue", ModelUnavailable("gone"))

    assert result.state["stop_reason"] == "llm_unavailable"
    assert result.state["error"].code == "LLM_UNAVAILABLE"


async def test_the_call_budget_is_enforced(source, monkeypatch) -> None:
    monkeypatch.setenv("MAX_LLM_CALLS_PER_ANALYSIS", "1")
    from app.core.config import get_settings
    get_settings.cache_clear()

    bad = reply(sql="SELECT o.nope FROM orders o")
    result, provider = await run(source, "revenue", bad, bad, bad)

    assert result.state["stop_reason"] == "budget_exhausted"
    assert len(provider.requests) == 1, "the over-budget call must never reach the model"


async def test_an_unknown_data_source_fails_cleanly(monkeypatch) -> None:
    import uuid

    from app.services.datasource_service import DataSourceNotFound

    with pytest.raises(DataSourceNotFound):
        await run_analysis(uuid.uuid4(), "revenue", embeddings=EMBEDDINGS)
