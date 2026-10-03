"""Read-only access to analytical data sources, behind one interface."""

from app.connectors.base import DataConnector
from app.connectors.errors import (
    ConnectionFailed,
    ConnectorError,
    QueryExecutionError,
    QueryTimeout,
    SchemaIntrospectionError,
    UnsupportedDataSource,
)
from app.connectors.factory import SUPPORTED_TYPES, create_connector
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

__all__ = [
    "SUPPORTED_TYPES",
    "ColumnInfo",
    "ColumnProfile",
    "ConnectionFailed",
    "ConnectionTestResult",
    "ConnectorError",
    "DataConnector",
    "ForeignKey",
    "QueryExecutionError",
    "QueryResult",
    "QueryTimeout",
    "ResultColumn",
    "SchemaIntrospectionError",
    "SchemaSnapshot",
    "TableInfo",
    "UnsupportedDataSource",
    "create_connector",
]
