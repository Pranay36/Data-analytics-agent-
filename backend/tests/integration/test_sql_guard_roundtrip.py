"""SQL that leaves the guard must still run.

The guard does not return the SQL it was given — it returns SQL regenerated
from the syntax tree, with a row cap applied. A rewrite that produced subtly
invalid SQL would turn every approved query into a database error, so the unit
tests are not enough on their own: the output has to be executed.
"""

from __future__ import annotations

import psycopg
import pytest

from app.core.config import get_settings
from app.sql_guard import validate_sql

pytestmark = pytest.mark.integration

DEMO_TABLES = {
    "public.orders", "public.customers", "public.products",
    "public.order_items", "public.payments", "public.refunds",
}

# Representative of what the Query Agent is expected to produce.
ANALYTICAL_QUERIES = [
    "SELECT sum(total_amount) AS revenue FROM orders WHERE status = 'SUCCESS'",
    """
    SELECT shipping_region, sum(total_amount) AS revenue
    FROM orders WHERE status = 'SUCCESS'
      AND order_date >= DATE '2026-06-01' AND order_date < DATE '2026-07-01'
    GROUP BY shipping_region ORDER BY revenue DESC
    """,
    """
    WITH monthly AS (
        SELECT date_trunc('month', order_date) AS month, sum(total_amount) AS revenue
        FROM orders WHERE status = 'SUCCESS' GROUP BY 1
    )
    SELECT month, revenue FROM monthly ORDER BY month
    """,
    """
    SELECT p.category,
           sum(oi.line_amount) FILTER (
               WHERE o.order_date >= DATE '2026-05-01'
                 AND o.order_date < DATE '2026-06-01') AS previous_value,
           sum(oi.line_amount) FILTER (
               WHERE o.order_date >= DATE '2026-06-01'
                 AND o.order_date < DATE '2026-07-01') AS current_value
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN products p ON p.id = oi.product_id
    WHERE o.status = 'SUCCESS' AND o.shipping_region = 'South'
    GROUP BY p.category
    """,
    """
    SELECT c.name, sum(o.total_amount) AS revenue
    FROM orders o JOIN customers c ON c.id = o.customer_id
    WHERE o.status = 'SUCCESS'
    GROUP BY c.name ORDER BY revenue DESC LIMIT 10
    """,
]


@pytest.fixture(scope="module")
def conn():
    url = get_settings().demo_analytics_url
    if not url:
        pytest.skip("DEMO_ANALYTICS_URL not set")
    try:
        connection = psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Demo database unreachable: {exc}")
    with connection:
        yield connection


@pytest.mark.parametrize("sql", ANALYTICAL_QUERIES)
def test_guarded_sql_executes(conn, sql: str) -> None:
    result = validate_sql(sql, dialect="postgres", allowed_tables=DEMO_TABLES, max_rows=500)
    assert result.ok, result.errors

    rows = conn.execute(result.safe_sql).fetchall()
    assert len(rows) <= 500


def test_row_cap_is_enforced_against_real_data(conn) -> None:
    """24k orders exist; the guard must cap what comes back."""
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] > 500

    result = validate_sql("SELECT id FROM orders", dialect="postgres", max_rows=500)
    assert len(conn.execute(result.safe_sql).fetchall()) == 500


def test_rewrite_preserves_meaning(conn) -> None:
    """The regenerated SQL must return the same answer as the original."""
    original = (
        "SELECT sum(total_amount) AS revenue FROM orders "
        "WHERE status = 'SUCCESS' AND shipping_region = 'South'"
    )
    result = validate_sql(original, dialect="postgres", allowed_tables=DEMO_TABLES)

    assert conn.execute(original).fetchone()[0] == conn.execute(result.safe_sql).fetchone()[0]
