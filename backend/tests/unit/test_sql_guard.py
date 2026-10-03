"""The SQL guard is the last thing standing between a language model and a
database, so it gets the most thorough tests in the project.

Cases are table-driven: adding a newly discovered bypass should mean adding one
line, not writing a new test.
"""

from __future__ import annotations

import pytest

from app.sql_guard import validate_sql

DEMO_TABLES = {"public.orders", "public.customers", "public.products", "public.order_items"}
DEMO_COLUMNS = {
    "public.orders": {
        "id", "customer_id", "order_date", "status", "channel",
        "payment_method", "shipping_region", "total_amount", "discount_amount",
    },
    "public.customers": {"id", "name", "email", "city", "region", "segment", "signup_date"},
}


# ── Queries that must be allowed ─────────────────────────────────────────────
ALLOWED = [
    pytest.param("SELECT id FROM orders", id="simple"),
    pytest.param(
        "SELECT shipping_region, sum(total_amount) AS revenue FROM orders "
        "WHERE status = 'SUCCESS' GROUP BY 1 ORDER BY 2 DESC",
        id="aggregate",
    ),
    pytest.param("SELECT count(*) AS n FROM orders", id="count-star-is-not-select-star"),
    pytest.param(
        "WITH monthly AS (SELECT date_trunc('month', order_date) AS m, "
        "sum(total_amount) AS rev FROM orders WHERE status = 'SUCCESS' GROUP BY 1) "
        "SELECT m, rev FROM monthly ORDER BY m",
        id="cte",
    ),
    pytest.param(
        "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
        id="join-with-aliases",
    ),
    pytest.param(
        "SELECT shipping_region, total_amount, "
        "rank() OVER (PARTITION BY shipping_region ORDER BY total_amount DESC) AS r "
        "FROM orders",
        id="window-function",
    ),
    pytest.param(
        "SELECT id FROM orders WHERE customer_id IN (SELECT id FROM customers)",
        id="subquery",
    ),
    pytest.param("SELECT id FROM orders UNION SELECT id FROM customers", id="union"),
    pytest.param(
        "SELECT sum(total_amount) FILTER (WHERE status = 'SUCCESS') AS rev FROM orders",
        id="filter-clause",
    ),
    pytest.param("SELECT id FROM orders -- a trailing comment", id="comment"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_legitimate_analytical_queries(sql: str) -> None:
    result = validate_sql(sql, dialect="postgres", allowed_tables=DEMO_TABLES)
    assert result.ok, result.errors
    assert result.safe_sql


# ── Queries that must be rejected ────────────────────────────────────────────
# `reason` is a substring the error must contain, so a case cannot pass by
# being rejected for an unrelated reason.
REJECTED = [
    # Multiple statements — the classic injection shape. A prefix check sees
    # only "SELECT" and lets this through.
    pytest.param("SELECT 1; DROP TABLE orders", "one statement", id="multi-statement"),
    pytest.param("SELECT id FROM orders; DELETE FROM orders", "one statement", id="multi-delete"),
    # Writes hidden inside a query that still *starts* with SELECT or WITH.
    pytest.param(
        "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
        "DELETE",
        id="delete-inside-cte",
    ),
    pytest.param(
        "WITH x AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT id FROM x",
        "INSERT",
        id="insert-inside-cte",
    ),
    pytest.param("SELECT id INTO backup FROM orders", "INTO", id="select-into-writes"),
    # Plain DML and DDL.
    pytest.param("DELETE FROM orders", "read queries", id="delete"),
    pytest.param("UPDATE orders SET total_amount = 0", "read queries", id="update"),
    pytest.param("INSERT INTO orders (id) VALUES (1)", "read queries", id="insert"),
    pytest.param("DROP TABLE orders", "read queries", id="drop"),
    pytest.param("ALTER TABLE orders ADD COLUMN x int", "read queries", id="alter"),
    pytest.param("TRUNCATE TABLE orders", "read queries", id="truncate"),
    pytest.param("CREATE TABLE t (a int)", "read queries", id="create"),
    pytest.param("GRANT SELECT ON orders TO someone", "read queries", id="grant"),
    pytest.param("SET work_mem = '1GB'", "read queries", id="set"),
    pytest.param("COPY orders FROM '/etc/passwd'", "read queries", id="copy"),
    # Functions that escape the database.
    pytest.param("SELECT pg_sleep(60)", "pg_sleep", id="pg_sleep"),
    pytest.param("SELECT pg_read_file('/etc/passwd')", "pg_read_file", id="pg_read_file"),
    pytest.param(
        "SELECT id FROM orders WHERE total_amount > (SELECT pg_sleep(5))::numeric",
        "pg_sleep",
        id="function-nested-in-subquery",
    ),
    # Explicit column lists keep results small and readable.
    pytest.param("SELECT * FROM orders", "SELECT *", id="select-star"),
    pytest.param("SELECT o.* FROM orders o", "SELECT *", id="qualified-star"),
    # Nonexistent tables.
    pytest.param("SELECT id FROM sales", "not available", id="unknown-table"),
    # Not parseable at all.
    pytest.param("SELECT FROM WHERE", "parse", id="syntax-error"),
    pytest.param("", "Empty", id="empty"),
]


@pytest.mark.parametrize(("sql", "reason"), REJECTED)
def test_rejects_unsafe_queries(sql: str, reason: str) -> None:
    result = validate_sql(sql, dialect="postgres", allowed_tables=DEMO_TABLES)
    assert not result.ok, f"should have been rejected: {sql}"
    assert reason.lower() in result.error_text.lower(), (
        f"rejected for the wrong reason: {result.error_text}"
    )


# ── Row limits ───────────────────────────────────────────────────────────────
def test_adds_limit_when_missing() -> None:
    result = validate_sql("SELECT id FROM orders", dialect="postgres", max_rows=500)
    assert result.ok
    assert "LIMIT 500" in result.safe_sql
    assert result.limit_applied == 500


def test_keeps_a_stricter_limit() -> None:
    result = validate_sql("SELECT id FROM orders LIMIT 10", dialect="postgres", max_rows=500)
    assert result.ok
    assert "LIMIT 10" in result.safe_sql
    assert result.limit_applied is None


def test_tightens_an_excessive_limit() -> None:
    result = validate_sql("SELECT id FROM orders LIMIT 100000", dialect="postgres", max_rows=500)
    assert result.ok
    assert "LIMIT 500" in result.safe_sql
    assert "100000" not in result.safe_sql


def test_inner_limit_does_not_satisfy_the_outer_query() -> None:
    """A keyword check sees "LIMIT" and adds nothing — then returns every row."""
    result = validate_sql(
        "SELECT id FROM (SELECT id FROM orders LIMIT 5) t", dialect="postgres", max_rows=500
    )
    assert result.ok
    assert result.limit_applied == 500
    assert result.safe_sql.rstrip().endswith("LIMIT 500")


def test_union_gets_an_outer_limit() -> None:
    result = validate_sql(
        "SELECT id FROM orders UNION SELECT id FROM customers", dialect="postgres", max_rows=500
    )
    assert result.ok
    assert "LIMIT 500" in result.safe_sql


# ── Table extraction ─────────────────────────────────────────────────────────
def test_reports_tables_used() -> None:
    result = validate_sql(
        "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
        dialect="postgres",
        allowed_tables=DEMO_TABLES,
    )
    assert set(result.tables) == {"orders", "customers"}


def test_cte_names_are_not_treated_as_tables() -> None:
    """Otherwise every CTE looks like a table missing from the allowlist."""
    result = validate_sql(
        "WITH recent AS (SELECT id FROM orders) SELECT id FROM recent",
        dialect="postgres",
        allowed_tables=DEMO_TABLES,
    )
    assert result.ok, result.errors
    assert result.tables == ["orders"]


def test_schema_qualified_and_bare_names_both_match_the_allowlist() -> None:
    for sql in ("SELECT id FROM orders", "SELECT id FROM public.orders"):
        assert validate_sql(sql, dialect="postgres", allowed_tables=DEMO_TABLES).ok


# ── Column checking ──────────────────────────────────────────────────────────
def test_unknown_column_is_rejected_with_a_suggestion() -> None:
    """The suggestion is what makes the repair attempt likely to succeed."""
    result = validate_sql(
        "SELECT o.region FROM orders o",
        dialect="postgres",
        allowed_tables=DEMO_TABLES,
        known_columns=DEMO_COLUMNS,
    )
    assert not result.ok
    assert "shipping_region" in result.error_text


def test_known_columns_pass() -> None:
    result = validate_sql(
        "SELECT o.shipping_region, o.total_amount FROM orders o",
        dialect="postgres",
        allowed_tables=DEMO_TABLES,
        known_columns=DEMO_COLUMNS,
    )
    assert result.ok, result.errors


def test_unqualified_columns_are_not_guessed_at() -> None:
    """A false rejection wastes a repair attempt; the database catches these."""
    result = validate_sql(
        "SELECT whatever FROM orders",
        dialect="postgres",
        allowed_tables=DEMO_TABLES,
        known_columns=DEMO_COLUMNS,
    )
    assert result.ok


# ── Dialects ─────────────────────────────────────────────────────────────────
DIALECT_CASES = [
    pytest.param("duckdb", "SELECT * FROM read_csv('/etc/passwd')", "read_csv", id="duckdb-csv"),
    pytest.param(
        "duckdb", "SELECT * FROM read_parquet('s3://x/y')", "read_parquet", id="duckdb-parquet"
    ),
    pytest.param("duckdb", "SELECT * FROM glob('/**')", "glob", id="duckdb-glob"),
    pytest.param(
        "clickhouse", "SELECT * FROM url('http://evil/x', CSV)", "url", id="clickhouse-url"
    ),
    pytest.param("clickhouse", "SELECT * FROM file('/etc/passwd')", "file", id="clickhouse-file"),
    pytest.param(
        "clickhouse",
        "SELECT * FROM remote('other:9000', system, tables)",
        "remote",
        id="clickhouse-remote",
    ),
]


@pytest.mark.parametrize(("dialect", "sql", "reason"), DIALECT_CASES)
def test_rejects_file_and_network_functions_per_dialect(
    dialect: str, sql: str, reason: str
) -> None:
    result = validate_sql(sql, dialect=dialect)
    assert not result.ok, f"{dialect} allowed {sql}"
    assert reason in result.error_text.lower()


def test_dialect_specific_syntax_parses() -> None:
    """ClickHouse SQL must not be rejected for merely not being Postgres."""
    result = validate_sql(
        "SELECT toStartOfMonth(order_date) AS m, sumIf(total_amount, status = 'SUCCESS') AS rev "
        "FROM orders GROUP BY m",
        dialect="clickhouse",
    )
    assert result.ok, result.errors


def test_unsupported_dialect_is_rejected_loudly() -> None:
    with pytest.raises(ValueError, match="Unsupported dialect"):
        validate_sql("SELECT 1", dialect="oracle")
