"""The evaluation dataset must itself be sound.

A wrong gold query makes every score downstream meaningless without any sign of it, so
the cases are validated like code: each gold query has to pass the safety guard,
execute, and return real data.
"""

from __future__ import annotations

import psycopg
import pytest

from app.core.config import get_settings
from app.evaluation.cases import load_suite
from app.sql_guard import validate_sql

pytestmark = pytest.mark.integration

SUITE = load_suite("core")
GOLD_CASES = [case for case in SUITE.cases if case.gold_sql]


@pytest.fixture(scope="module")
def conn():
    url = get_settings().demo_analytics_url
    if not url:
        pytest.skip("DEMO_ANALYTICS_URL not set")
    ro = url.replace("shopsphere:shopsphere@", "insightflow_ro:insightflow_ro@")
    try:
        connection = psycopg.connect(ro, connect_timeout=5)
    except psycopg.OperationalError:
        pytest.skip("Demo database unreachable")
    with connection:
        yield connection


def test_ids_are_unique_and_the_suite_is_a_useful_size() -> None:
    ids = [case.id for case in SUITE.cases]
    assert len(ids) == len(set(ids))
    assert len(SUITE.cases) >= 25


def test_the_suite_covers_the_failure_modes_it_exists_to_catch() -> None:
    categories = {case.category for case in SUITE.cases}
    assert {"business_rule", "root_cause", "unanswerable", "safety", "distractor"} <= categories


@pytest.mark.parametrize("case", GOLD_CASES, ids=lambda c: c.id)
def test_the_gold_query_is_safe_runs_and_returns_data(case, conn) -> None:
    verdict = validate_sql(case.gold_sql, dialect="postgres", max_rows=500)
    assert verdict.ok, f"gold SQL rejected by the guard: {verdict.error_text}"

    rows = conn.execute(verdict.safe_sql).fetchall()
    assert rows, "the gold query returned no rows"
    assert any(cell is not None for row in rows for cell in row), "the gold result is all NULL"


def test_scalar_gold_queries_return_one_row(conn) -> None:
    for case in GOLD_CASES:
        if case.compare == "scalar":
            assert len(conn.execute(case.gold_sql).fetchall()) == 1, case.id


def test_top_k_cases_return_enough_rows(conn) -> None:
    for case in GOLD_CASES:
        if case.compare == "top_k":
            assert len(conn.execute(case.gold_sql).fetchall()) >= case.top_k, case.id


def test_every_case_that_expects_an_answer_can_be_checked() -> None:
    """An 'answer' case with neither a gold query, a path, nor mentions asserts nothing."""
    for case in SUITE.cases:
        if case.expected_outcome == "answer" and not case.is_retrieval_only:
            assert case.gold_sql or case.expect_path or case.expect_mentions, (
                f"{case.id} has nothing to check its answer against"
            )


def test_the_planted_revenue_trap_is_large_enough_to_matter(conn) -> None:
    """If ignoring `status` barely changed the answer, the trap cases would prove nothing."""
    correct = conn.execute("SELECT SUM(total_amount) FROM orders WHERE status='SUCCESS'").fetchone()[0]
    naive = conn.execute("SELECT SUM(total_amount) FROM orders").fetchone()[0]
    assert float(naive) > float(correct) * 1.05
