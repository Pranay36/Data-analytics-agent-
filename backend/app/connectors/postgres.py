"""PostgreSQL connector.

Read-only is set on the connection (`default_transaction_read_only=on`), and the
timeout is `statement_timeout`, which Postgres enforces server-side — so a
runaway query is cancelled by the database rather than merely abandoned by us.
A client-side timeout would leave the query consuming resources after we stop
waiting for it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import psycopg
from psycopg import sql as pg_sql
from psycopg.rows import tuple_row

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
    ForeignKey,
    QueryResult,
    ResultColumn,
    SchemaSnapshot,
    TableInfo,
)

logger = logging.getLogger(__name__)

# Never introspected: internal bookkeeping, not business data.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


class PostgresConnector(DataConnector):
    engine = "postgres"
    dialect = "postgres"

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        super().__init__(config, secret)
        self._conn: psycopg.Connection | None = None
        self._schemas: list[str] = config.get("schemas") or ["public"]

    # ── Connection ───────────────────────────────────────────────────────────
    def _connection_kwargs(self, timeout_seconds: int) -> dict[str, Any]:
        return {
            "host": self.config.get("host", "localhost"),
            "port": int(self.config.get("port", 5432)),
            "dbname": self.config["database"],
            "user": self.config.get("username"),
            "password": self._secret,
            "connect_timeout": 10,
            "sslmode": self.config.get("sslmode", "prefer"),
            # Read-only and the statement timeout are applied by the server for
            # the whole session, so they hold for every query on it.
            "options": (
                f"-c default_transaction_read_only=on "
                f"-c statement_timeout={timeout_seconds * 1000} "
                f"-c idle_in_transaction_session_timeout=30000"
            ),
            "application_name": "insightflow",
        }

    def _connect(self, timeout_seconds: int = 20) -> psycopg.Connection:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            self._conn = psycopg.connect(
                **self._connection_kwargs(timeout_seconds),
                autocommit=True,
                row_factory=tuple_row,
            )
        except psycopg.OperationalError as exc:
            raise ConnectionFailed(_readable(exc), original=exc) from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    # ── Interface ────────────────────────────────────────────────────────────
    def test_connection(self) -> ConnectionTestResult:
        started = time.perf_counter()
        try:
            conn = self._connect()
            version = conn.execute("SELECT version()").fetchone()
            return ConnectionTestResult(
                ok=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
                server_version=str(version[0]).split(",")[0] if version else None,
            )
        except ConnectionFailed as exc:
            return ConnectionTestResult(ok=False, error=exc.message)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
            return ConnectionTestResult(ok=False, error=_readable(exc))

    def execute(
        self, sql: str, *, max_rows: int = 500, timeout_seconds: int = 20
    ) -> QueryResult:
        conn = self._connect(timeout_seconds)
        started = time.perf_counter()

        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = {int(timeout_seconds * 1000)}")
                cursor.execute(sql)  # type: ignore[arg-type]

                # One extra row tells us whether more existed beyond the cap.
                fetched = cursor.fetchmany(max_rows + 1) if cursor.description else []
                truncated = len(fetched) > max_rows
                rows = [normalize_row(row) for row in fetched[:max_rows]]

                columns = [
                    ResultColumn(
                        name=column.name,
                        normalized_type=normalize_type(
                            _type_name(conn, column.type_code) or ""
                        ),
                    )
                    for column in (cursor.description or [])
                ]
        except psycopg.errors.QueryCanceled as exc:
            raise QueryTimeout(
                f"Query exceeded the {timeout_seconds}s limit. "
                "Aggregate further or narrow the date range.",
                original=exc,
            ) from exc
        except psycopg.OperationalError as exc:
            self.close()
            raise ConnectionFailed(_readable(exc), original=exc) from exc
        except psycopg.Error as exc:
            raise QueryExecutionError(_readable(exc), original=exc) from exc

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
            tables = self._fetch_tables(conn)
            self._attach_columns(conn, tables)
            foreign_keys = self._fetch_foreign_keys(conn)
        except psycopg.Error as exc:
            raise SchemaIntrospectionError(_readable(exc), original=exc) from exc

        return SchemaSnapshot(
            tables=[table for table in tables.values() if table.columns],
            foreign_keys=foreign_keys,
        )

    def _fetch_tables(self, conn: psycopg.Connection) -> dict[str, TableInfo]:
        rows = conn.execute(
            """
            SELECT c.relnamespace::regnamespace::text AS schema_name,
                   c.relname                          AS table_name,
                   CASE c.relkind WHEN 'v' THEN 'view'
                                  WHEN 'm' THEN 'view'
                                  ELSE 'table' END    AS object_type,
                   obj_description(c.oid, 'pg_class') AS description,
                   CASE WHEN c.reltuples < 0 THEN NULL
                        ELSE c.reltuples::bigint END  AS row_estimate
            FROM pg_class c
            WHERE c.relkind IN ('r', 'p', 'v', 'm')
              AND c.relnamespace::regnamespace::text = ANY(%s)
            ORDER BY 1, 2
            """,
            (self._schemas,),
        ).fetchall()

        return {
            f"{schema}.{name}": TableInfo(
                schema_name=schema,
                table_name=name,
                object_type=object_type,  # type: ignore[arg-type]
                description=description,
                row_count_estimate=row_estimate,
            )
            for schema, name, object_type, description, row_estimate in rows
        }

    def _attach_columns(self, conn: psycopg.Connection, tables: dict[str, TableInfo]) -> None:
        rows = conn.execute(
            """
            SELECT c.table_schema, c.table_name, c.column_name,
                   c.data_type, c.is_nullable = 'YES' AS nullable,
                   col_description(
                       format('%%I.%%I', c.table_schema, c.table_name)::regclass::oid,
                       c.ordinal_position
                   ) AS description
            FROM information_schema.columns c
            WHERE c.table_schema = ANY(%s)
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """,
            (self._schemas,),
        ).fetchall()

        for schema, table_name, column_name, data_type, nullable, description in rows:
            table = tables.get(f"{schema}.{table_name}")
            if table is None:
                continue
            table.columns.append(
                ColumnInfo(
                    name=column_name,
                    data_type=data_type,
                    normalized_type=normalize_type(data_type),
                    nullable=nullable,
                    description=description,
                )
            )

    def _fetch_foreign_keys(self, conn: psycopg.Connection) -> list[ForeignKey]:
        """Join paths, read straight from the constraints the database already has.

        Wrong joins are a common Text-to-SQL failure, and this removes the guesswork
        for free wherever the schema declares its relationships.
        """
        rows = conn.execute(
            """
            SELECT src_ns.nspname  || '.' || src.relname  AS from_table,
                   src_col.attname                        AS from_column,
                   tgt_ns.nspname  || '.' || tgt.relname  AS to_table,
                   tgt_col.attname                        AS to_column
            FROM pg_constraint con
            JOIN pg_class     src     ON src.oid = con.conrelid
            JOIN pg_namespace src_ns  ON src_ns.oid = src.relnamespace
            JOIN pg_class     tgt     ON tgt.oid = con.confrelid
            JOIN pg_namespace tgt_ns  ON tgt_ns.oid = tgt.relnamespace
            JOIN pg_attribute src_col ON src_col.attrelid = con.conrelid
                                     AND src_col.attnum = con.conkey[1]
            JOIN pg_attribute tgt_col ON tgt_col.attrelid = con.confrelid
                                     AND tgt_col.attnum = con.confkey[1]
            WHERE con.contype = 'f'
              AND src_ns.nspname = ANY(%s)
            """,
            (self._schemas,),
        ).fetchall()

        return [
            ForeignKey(
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            )
            for from_table, from_column, to_table, to_column in rows
        ]

    def profile_columns(
        self, schema_name: str, table_name: str, columns: list[str]
    ) -> dict[str, ColumnProfile]:
        conn = self._connect()
        profiles: dict[str, ColumnProfile] = {}
        table = pg_sql.Identifier(schema_name, table_name)

        for column in columns:
            identifier = pg_sql.Identifier(column)
            try:
                # Sampled, not exhaustive: profiling must not become the slowest
                # part of registering a data source.
                row = conn.execute(
                    pg_sql.SQL(
                        """
                        SELECT count(DISTINCT {col}) AS distinct_count,
                               avg(CASE WHEN {col} IS NULL THEN 1.0 ELSE 0.0 END) AS null_fraction
                        FROM (SELECT {col} FROM {table} LIMIT 20000) sample
                        """
                    ).format(col=identifier, table=table)
                ).fetchone()
                distinct_count = int(row[0]) if row and row[0] is not None else None
                null_fraction = float(row[1]) if row and row[1] is not None else None

                samples: list[str] = []
                if distinct_count is not None and 0 < distinct_count <= 50:
                    sample_rows = conn.execute(
                        pg_sql.SQL(
                            "SELECT DISTINCT {col}::text FROM {table} "
                            "WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 10"
                        ).format(col=identifier, table=table)
                    ).fetchall()
                    samples = [str(value[0]) for value in sample_rows]

                profiles[column] = ColumnProfile(
                    distinct_count=distinct_count,
                    null_fraction=null_fraction,
                    sample_values=samples,
                )
            except psycopg.Error as exc:
                # Best-effort by contract: a column we cannot profile is a
                # slightly worse prompt, not a failed sync.
                logger.debug(
                    "could not profile column",
                    extra={"table": f"{schema_name}.{table_name}", "column": column,
                           "error": str(exc)},
                )
                profiles[column] = ColumnProfile()

        return profiles


def _type_name(conn: psycopg.Connection, type_code: int) -> str | None:
    """Resolve a result column's type OID to its name."""
    try:
        row = conn.execute("SELECT %s::oid::regtype::text", (type_code,)).fetchone()
        return str(row[0]) if row else None
    except psycopg.Error:
        return None


def _readable(exc: Exception) -> str:
    """First line of a database error, without the password or the full DSN."""
    message = str(exc).strip().split("\n")[0]
    return message or type(exc).__name__
