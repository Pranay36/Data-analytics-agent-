"""API shapes for data sources.

The response model has no secret field at all — only `has_secret`. Omitting it
from the type, rather than blanking it at runtime, means a future code path
cannot leak it by forgetting to redact.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

SourceType = Literal["postgres", "clickhouse", "csv"]


class PostgresConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    database: str
    username: str
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    sslmode: str = "prefer"


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["postgres"]
    config: PostgresConfig
    password: SecretStr | None = None
    business_context: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestRequest(BaseModel):
    type: Literal["postgres"]
    config: PostgresConfig
    password: SecretStr | None = None


class DataSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    config: dict[str, Any]
    has_secret: bool
    status: str
    status_message: str | None
    business_context: dict[str, Any]
    table_count: int = 0
    last_synced_at: datetime | None

    model_config = {"from_attributes": True}


class ColumnOut(BaseModel):
    name: str
    data_type: str
    normalized_type: str
    description: str | None
    sample_values: list[Any] | None
    distinct_count: int | None
    is_dimension: bool


class TableOut(BaseModel):
    id: uuid.UUID
    schema_name: str
    table_name: str
    description: str | None
    row_count_estimate: int | None
    is_queryable: bool
    columns: list[ColumnOut]


class RelationshipOut(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    source: str


class SchemaOut(BaseModel):
    tables: list[TableOut]
    relationships: list[RelationshipOut]


class SyncResult(BaseModel):
    tables: int
    columns: int
    relationships: int
    duration_ms: int
