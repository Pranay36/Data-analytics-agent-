"""One suite, run against every connector.

This is what makes the abstraction real rather than aspirational. When
ClickHouse is added, it either passes these unchanged or the abstraction was
wrong — and finding that out is the whole point of having an interface.

Both connectors are pointed at the same ShopSphere data, so their answers are
directly comparable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.connectors import (
    ConnectorError,
    QueryExecutionError,
    create_connector,
)
from app.connectors.csv_import import build_duckdb_from_csvs
from app.core.config import get_settings

pytestmark = pytest.mark.integration

REPO_DIR = Path(__file__).resolve().parents[3]
CSV_DIR = REPO_DIR / "demo_data" / "generated"


def _postgres_connector():
    url = get_settings().demo_analytics_url
    if not url:
        pytest.skip("DEMO_ANALYTICS_URL not set")

    # postgresql://user:pass@host:port/db
    rest = url.split("://", 1)[1]
    credentials, location = rest.split("@", 1)
    username, password = credentials.split(":", 1)
    hostport, database = location.split("/", 1)
    host, _, port = hostport.partition(":")

    connector = create_connector(
        "postgres",
        {
            "host": host,
            "port": int(port or 5432),
            "database": database.split("?")[0],
            "username": username,
            "schemas": ["public"],
        },
        secret=password,
    )
    if not connector.test_connection().ok:
        pytest.skip("Demo Postgres unreachable")
    return connector


def _duckdb_connector(tmp_path_factory):
    if not (CSV_DIR / "orders.csv").exists():
        pytest.skip("No generated CSVs — run app.scripts.generate_demo_data")

    target = tmp_path_factory.mktemp("duckdb") / "demo.duckdb"
    build_duckdb_from_csvs(
        [CSV_DIR / f"{name}.csv" for name in ("orders", "customers", "products", "order_items")],
        target,
    )
    return create_connector("duckdb", {"path": str(target)})


@pytest.fixture(scope="module", params=["postgres", "duckdb"])
def connector(request, tmp_path_factory):
    built = (
        _postgres_connector()
        if request.param == "postgres"
        else _duckdb_connector(tmp_path_factory)
    )
    with built as conn:
        yield conn


# ── The contract ─────────────────────────────────────────────────────────────
def test_reports_a_healthy_connection(connector) -> None:
    result = connector.test_connection()
    assert result.ok
    assert result.latency_ms is not None
    assert result.server_version


def test_exposes_a_dialect_for_the_sql_layer(connector) -> None:
    """The guard parses with this, and the prompt is chosen by it."""
    from app.sql_guard import SUPPORTED_DIALECTS

    assert connector.dialect in SUPPORTED_DIALECTS


def test_discovers_the_demo_tables(connector) -> None:
    snapshot = connector.introspect_schema()
    names = {table.table_name for table in snapshot.tables}
    assert {"orders", "customers", "products", "order_items"} <= names

    orders = next(table for table in snapshot.tables if table.table_name == "orders")
    assert {"id", "status", "total_amount", "shipping_region"} <= {
        column.name for column in orders.columns
    }


def test_normalises_types_across_engines(connector) -> None:
    """`numeric(12,2)` and DuckDB's `DECIMAL(18,3)` must both read as decimal."""
    orders = next(
        table
        for table in connector.introspect_schema().tables
        if table.table_name == "orders"
    )
    by_name = {column.name: column for column in orders.columns}

    assert by_name["total_amount"].normalized_type in {"decimal", "float"}
    assert by_name["id"].normalized_type == "integer"
    assert by_name["status"].normalized_type == "string"
    assert by_name["order_date"].normalized_type in {"timestamp", "date"}


def test_executes_a_query_and_returns_json_safe_values(connector) -> None:
    result = connector.execute(
        "SELECT shipping_region, sum(total_amount) AS revenue FROM orders "
        "WHERE status = 'SUCCESS' GROUP BY shipping_region ORDER BY 1",
        max_rows=10,
    )

    assert result.column_names == ["shipping_region", "revenue"]
    assert result.row_count == 4
    assert not result.truncated
    assert result.execution_ms >= 0

    import json

    json.dumps(result.rows)  # must not raise: Decimal/datetime are normalised

    regions = {row[0] for row in result.rows}
    assert regions == {"North", "South", "East", "West"}


def test_caps_rows_and_admits_truncation(connector) -> None:
    result = connector.execute("SELECT id FROM orders", max_rows=5)
    assert result.row_count == 5
    assert result.truncated, "more rows existed; truncation must be reported"


def test_invalid_sql_raises_a_repairable_error(connector) -> None:
    """`is_query_fault` is what tells the graph to ask the model for a fix."""
    with pytest.raises(QueryExecutionError) as exc_info:
        connector.execute("SELECT no_such_column FROM orders")

    assert exc_info.value.is_query_fault
    assert exc_info.value.message


def test_profiling_finds_the_status_values(connector) -> None:
    """These sample values are what stop the model inventing `status='completed'`."""
    profiles = connector.profile_columns("public", "orders", ["status", "shipping_region"])

    status = profiles["status"]
    assert status.distinct_count == 4
    assert set(status.sample_values) == {"SUCCESS", "FAILED", "CANCELLED", "PENDING"}
    assert status.is_low_cardinality

    assert profiles["shipping_region"].is_low_cardinality


def test_profiling_tolerates_a_bad_column(connector) -> None:
    """Best-effort by contract: a sync must not fail over one column."""
    profiles = connector.profile_columns("public", "orders", ["does_not_exist"])
    assert profiles["does_not_exist"].distinct_count is None


def test_both_engines_agree_on_revenue(connector) -> None:
    """Same question, same data, different engine — the answer must match."""
    result = connector.execute(
        "SELECT sum(total_amount) AS revenue FROM orders WHERE status = 'SUCCESS'"
    )
    assert float(result.rows[0][0]) == pytest.approx(251_928_207.60, rel=1e-6)


def test_connector_repr_hides_the_secret(connector) -> None:
    assert "password" not in repr(connector).lower()


# ── Engine-specific guarantees ───────────────────────────────────────────────
def test_postgres_connection_is_read_only() -> None:
    """Enforced by the session, beneath the SQL guard entirely."""
    connector = _postgres_connector()
    with connector, pytest.raises(ConnectorError) as exc_info:
        connector.execute("CREATE TABLE should_not_exist (id int)")
    assert "read-only" in str(exc_info.value).lower()


def test_duckdb_cannot_reach_the_filesystem(tmp_path_factory) -> None:
    """`enable_external_access=false` is the backstop if SQL ever gets through."""
    connector = _duckdb_connector(tmp_path_factory)
    with connector, pytest.raises(ConnectorError):
        connector.execute("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_csv_import_rejects_a_malformed_file(tmp_path) -> None:
    bad = tmp_path / "broken.csv"
    bad.write_text("a,b,c\n1,2\n", encoding="utf-8")

    from app.connectors.csv_import import CsvImportError

    with pytest.raises(CsvImportError):
        build_duckdb_from_csvs([bad], tmp_path / "out.duckdb")
    assert not (tmp_path / "out.duckdb").exists(), "a failed import must leave nothing behind"


def test_csv_filenames_become_usable_table_names() -> None:
    from app.connectors.csv_import import table_name_from_filename

    assert table_name_from_filename("Q3 Sales (final).csv") == "q3_sales_final"
    assert table_name_from_filename("2024-orders.csv") == "t_2024_orders"
    assert table_name_from_filename("select.csv") == "t_select"


def test_csv_import_accepts_a_genuine_single_column_file(tmp_path) -> None:
    """The ragged-file check must not reject a legitimately narrow CSV."""
    single = tmp_path / "emails.csv"
    single.write_text("email\na@example.com\nb@example.com\n", encoding="utf-8")

    loaded = build_duckdb_from_csvs([single], tmp_path / "ok.duckdb")
    assert loaded == {"emails": 2}
