"""DuckDB connector, used for uploaded CSV data.

Routing CSVs through DuckDB keeps one SQL pipeline for every data source: the
same Query Agent, the same SQL guard, the same profiler. The alternative — a
separate pandas path for files — would mean maintaining two of everything.

Two protections matter here, because DuckDB is an in-process engine with
filesystem reach:

* `read_only=True` makes writes impossible at the connection.
* `enable_external_access=false` stops generated SQL from reaching the
  filesystem or network at all, so `read_csv('/etc/passwd')` fails even if it
  somehow got past the SQL guard.

DuckDB has no server-side statement timeout, so queries are bounded with a
watchdog that calls `interrupt()`.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import duckdb

from app.connectors.base import DataConnector
from app.connectors.errors import (
    ConnectionFailed,
    QueryExecutionError,
    QueryTimeout,
    SchemaIntrospectionError,
)
from app.connectors.normalize import normalize_row, normalize_type
from app.connectors.types import (
    ColumnInfo,
    ColumnProfile,
    ConnectionTestResult,
    QueryResult,
    ResultColumn,
    SchemaSnapshot,
    TableInfo,
)

logger = logging.getLogger(__name__)

SCHEMA_NAME = "main"


class DuckDBConnector(DataConnector):
    engine = "duckdb"
    dialect = "duckdb"

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        super().__init__(config, secret)
        self.path = Path(config["path"])
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is not None:
            return self._conn
        if not self.path.exists():
            raise ConnectionFailed(f"Data file not found: {self.path.name}")

        try:
            conn = duckdb.connect(str(self.path), read_only=True)
            # Close off the filesystem and network for anything run on this
            # connection, then prevent the setting being changed back.
            conn.execute("SET enable_external_access = false")
            conn.execute("SET lock_configuration = true")
        except duckdb.Error as exc:
            raise ConnectionFailed(str(exc).splitlines()[0], original=exc) from exc

        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

    def test_connection(self) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            self._connect().execute("SELECT 1").fetchall()
            return ConnectionTestResult(
                ok=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                server_version=f"DuckDB {duckdb.__version__}",
            )
        except Exception as exc:  # noqa: BLE001 - reported to the user as text
            return ConnectionTestResult(ok=False, error=str(exc).splitlines()[0])

    def execute(
        self, sql: str, *, max_rows: int = 500, timeout_seconds: int = 20
    ) -> QueryResult:
        conn = self._connect()
        started = time.perf_counter()

        # No server-side timeout exists, so interrupt from another thread.
        timed_out = threading.Event()

        def interrupt() -> None:
            timed_out.set()
            conn.interrupt()

        watchdog = threading.Timer(timeout_seconds, interrupt)
        watchdog.start()
        try:
            cursor = conn.execute(sql)
            fetched = cursor.fetchmany(max_rows + 1) if cursor.description else []
            truncated = len(fetched) > max_rows
            rows = [normalize_row(row) for row in fetched[:max_rows]]
            columns = [
                ResultColumn(name=name, normalized_type=normalize_type(str(type_name)))
                for name, type_name in zip(
                    [desc[0] for desc in cursor.description or []],
                    [desc[1] for desc in cursor.description or []],
                    strict=False,
                )
            ]
        except duckdb.Error as exc:
            if timed_out.is_set():
                raise QueryTimeout(
                    f"Query exceeded the {timeout_seconds}s limit. "
                    "Aggregate further or narrow the date range.",
                    original=exc,
                ) from exc
            raise QueryExecutionError(str(exc).splitlines()[0], original=exc) from exc
        finally:
            watchdog.cancel()

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_ms=int((time.perf_counter() - started) * 1000),
        )

    def introspect_schema(self) -> SchemaSnapshot:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable = 'YES'
                FROM information_schema.columns
                WHERE table_schema = 'main'
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()
        except duckdb.Error as exc:
            raise SchemaIntrospectionError(str(exc).splitlines()[0], original=exc) from exc

        tables: dict[str, TableInfo] = {}
        for table_name, column_name, data_type, nullable in rows:
            table = tables.setdefault(
                table_name,
                TableInfo(schema_name=SCHEMA_NAME, table_name=table_name),
            )
            table.columns.append(
                ColumnInfo(
                    name=column_name,
                    data_type=str(data_type),
                    normalized_type=normalize_type(str(data_type)),
                    nullable=bool(nullable),
                )
            )

        for name, table in tables.items():
            try:
                count = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()
                table.row_count_estimate = int(count[0]) if count else None
            except duckdb.Error:
                pass

        # Uploaded files carry no foreign keys; relationships can be declared by
        # hand later if a user needs joins across them.
        return SchemaSnapshot(tables=list(tables.values()), foreign_keys=[])

    def profile_columns(
        self, schema_name: str, table_name: str, columns: list[str]
    ) -> dict[str, ColumnProfile]:
        conn = self._connect()
        profiles: dict[str, ColumnProfile] = {}

        for column in columns:
            quoted = f'"{column}"'
            try:
                row = conn.execute(
                    f'SELECT count(DISTINCT {quoted}), '
                    f'avg(CASE WHEN {quoted} IS NULL THEN 1.0 ELSE 0.0 END) '
                    f'FROM (SELECT {quoted} FROM "{table_name}" LIMIT 20000)'
                ).fetchone()
                distinct_count = int(row[0]) if row and row[0] is not None else None
                null_fraction = float(row[1]) if row and row[1] is not None else None

                samples: list[str] = []
                if distinct_count is not None and 0 < distinct_count <= 50:
                    sample_rows = conn.execute(
                        f'SELECT DISTINCT CAST({quoted} AS VARCHAR) FROM "{table_name}" '
                        f"WHERE {quoted} IS NOT NULL ORDER BY 1 LIMIT 10"
                    ).fetchall()
                    samples = [str(value[0]) for value in sample_rows]

                profiles[column] = ColumnProfile(
                    distinct_count=distinct_count,
                    null_fraction=null_fraction,
                    sample_values=samples,
                )
            except duckdb.Error as exc:
                logger.debug(
                    "could not profile column",
                    extra={"table": table_name, "column": column, "error": str(exc)},
                )
                profiles[column] = ColumnProfile()

        return profiles
