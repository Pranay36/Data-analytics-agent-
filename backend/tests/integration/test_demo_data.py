"""The demo dataset must mean what `ground_truth.json` says it means.

Evaluation later compares the agent's answers against that file, so if the data
and the file ever disagree, every downstream accuracy number is meaningless.
These tests re-derive the key facts with SQL and assert they match.

Skipped automatically when the demo database is not running.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from app.core.config import get_settings

REPO_DIR = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_DIR / "demo_data" / "generated" / "ground_truth.json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def truth() -> dict:
    if not GROUND_TRUTH.exists():
        pytest.skip("No ground_truth.json — run app.scripts.generate_demo_data")
    return json.loads(GROUND_TRUTH.read_text())


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


def scalar(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_row_counts_match(conn, truth) -> None:
    for table, expected in truth["row_counts"].items():
        assert scalar(conn, f"SELECT count(*) FROM {table}") == expected


def test_revenue_counts_successful_orders_only(conn, truth) -> None:
    """The headline trap: ignoring `status` inflates revenue."""
    correct = scalar(conn, "SELECT sum(total_amount) FROM orders WHERE status = 'SUCCESS'")
    naive = scalar(conn, "SELECT sum(total_amount) FROM orders")

    assert float(correct) == pytest.approx(
        truth["pattern_3_incomplete_orders"]["correct_total_revenue"], rel=1e-6
    )
    assert float(naive) == pytest.approx(
        truth["pattern_3_incomplete_orders"]["revenue_if_status_ignored"], rel=1e-6
    )
    # The trap has to be big enough that a wrong answer is obviously wrong.
    assert float(naive) > float(correct) * 1.05


def test_june_decline_is_concentrated_in_one_region(conn, truth) -> None:
    """Pattern 1, level one: a single region must dominate the decline."""
    rows = conn.execute(
        """
        SELECT shipping_region,
               sum(total_amount) FILTER (
                   WHERE order_date >= DATE '2026-05-01' AND order_date < DATE '2026-06-01') AS may,
               sum(total_amount) FILTER (
                   WHERE order_date >= DATE '2026-06-01' AND order_date < DATE '2026-07-01') AS june
        FROM orders
        WHERE status = 'SUCCESS'
          AND order_date >= DATE '2026-05-01' AND order_date < DATE '2026-07-01'
        GROUP BY 1
        """
    ).fetchall()

    changes = {region: float(june) - float(may) for region, may, june in rows}
    expected = truth["pattern_1_june_revenue_drop"]

    worst = min(changes, key=lambda region: changes[region])
    assert worst == expected["worst_region"] == "South"

    # Dominant enough that drilling into it is clearly the right move.
    share = changes[worst] / sum(value for value in changes.values() if value < 0)
    assert share > 0.6, f"{worst} explains only {share:.0%} of the decline"


def test_june_decline_drills_down_to_one_category(conn, truth) -> None:
    """Pattern 1, level two: within South, one category must dominate."""
    rows = conn.execute(
        """
        SELECT p.category,
               sum(oi.line_amount) FILTER (
                   WHERE o.order_date >= DATE '2026-05-01'
                     AND o.order_date < DATE '2026-06-01') AS may,
               sum(oi.line_amount) FILTER (
                   WHERE o.order_date >= DATE '2026-06-01'
                     AND o.order_date < DATE '2026-07-01') AS june
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p     ON p.id = oi.product_id
        WHERE o.status = 'SUCCESS'
          AND o.shipping_region = 'South'
          AND o.order_date >= DATE '2026-05-01' AND o.order_date < DATE '2026-07-01'
        GROUP BY 1
        """
    ).fetchall()

    pct = {
        category: (float(june) - float(may)) / float(may) * 100
        for category, may, june in rows
        if float(may) > 0
    }
    expected = truth["pattern_1_june_revenue_drop"]

    assert min(pct, key=lambda c: pct[c]) == expected["worst_category_in_south"] == "Electronics"
    assert pct["Electronics"] < -40


def test_refund_spike_is_visible(conn, truth) -> None:
    """Pattern 2: Home & Kitchen refunds jump in the final months."""
    rows = conn.execute(
        """
        SELECT to_char(r.refunded_at, 'YYYY-MM') AS month, count(*) AS n
        FROM refunds r
        JOIN order_items oi ON oi.id = r.order_item_id
        JOIN products p     ON p.id = oi.product_id
        WHERE p.category = 'Home & Kitchen'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()

    by_month = {month: count for month, count in rows}
    baseline = sum(count for month, count in by_month.items() if month < "2026-05") / max(
        len([m for m in by_month if m < "2026-05"]), 1
    )
    assert by_month["2026-06"] > baseline * 2, "refund spike is not distinguishable from noise"
    spike = truth["pattern_2_refund_spike"]
    assert spike["product"] in spike["top_refunded_products"]


def test_readonly_role_cannot_write(conn) -> None:
    """The outermost layer of the SQL safety model must actually hold."""
    url = get_settings().demo_analytics_url
    readonly_url = url.replace("shopsphere:shopsphere@", "insightflow_ro:insightflow_ro@")
    try:
        ro_conn = psycopg.connect(readonly_url, connect_timeout=5)
    except psycopg.OperationalError:
        pytest.skip("read-only role not created — run app.scripts.load_postgres")

    with ro_conn:
        assert ro_conn.execute("SELECT count(*) FROM orders").fetchone()[0] > 0
        for statement in (
            "DELETE FROM orders WHERE id = 1",
            "UPDATE orders SET total_amount = 0 WHERE id = 1",
            "DROP TABLE refunds",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                ro_conn.execute(statement)
            ro_conn.rollback()
