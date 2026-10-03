"""Normalised shapes every connector returns, whatever the engine underneath.

The point of these types is that nothing above the connector layer — not the
catalog, not the agents, not the graph — needs to know which database answered.
A ClickHouse `Nullable(Decimal(18,2))` and a Postgres `numeric(12,2)` both
arrive here as `normalized_type="decimal"`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NormalizedType = Literal[
    "integer", "float", "decimal", "string", "boolean", "date", "timestamp", "other"
]

NUMERIC_TYPES: frozenset[str] = frozenset({"integer", "float", "decimal"})
TEMPORAL_TYPES: frozenset[str] = frozenset({"date", "timestamp"})


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    """The engine's own type name, kept verbatim for display and debugging."""
    normalized_type: NormalizedType
    nullable: bool | None = None
    description: str | None = None
    """From the database's own column comment, when one exists."""


class ForeignKey(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class TableInfo(BaseModel):
    schema_name: str
    table_name: str
    object_type: Literal["table", "view"] = "table"
    description: str | None = None
    row_count_estimate: int | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


class SchemaSnapshot(BaseModel):
    """Everything discovered about a data source in one introspection pass."""

    tables: list[TableInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)


class ResultColumn(BaseModel):
    name: str
    normalized_type: NormalizedType


class QueryResult(BaseModel):
    """One query's output, with JSON-safe values.

    Rows are lists rather than dicts because query results routinely contain
    duplicate column names (`SELECT a.id, b.id ...`), which a dict would silently
    collapse.
    """

    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    """True when the query had more rows than the cap allowed through."""
    execution_ms: int = 0

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def column_index(self, name: str) -> int | None:
        for index, column in enumerate(self.columns):
            if column.name == name:
                return index
        return None


class ColumnProfile(BaseModel):
    """What the data in a column actually looks like.

    Sample values matter more than they might appear: knowing that `status`
    holds `SUCCESS`/`FAILED` rather than `completed`/`failed` is often the
    difference between correct SQL and a query that returns nothing.
    """

    distinct_count: int | None = None
    null_fraction: float | None = None
    sample_values: list[str] = Field(default_factory=list)

    @property
    def is_low_cardinality(self) -> bool:
        """Low-cardinality columns are the ones worth breaking a metric down by."""
        return self.distinct_count is not None and 1 < self.distinct_count <= 50


class ConnectionTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None
    server_version: str | None = None
