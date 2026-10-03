"""Connector failures, as typed exceptions.

The reference system returned errors as a fake result row (`[{"error": ...}]`)
so the agent could read them. We raise instead, and let the graph node decide
what the model sees (PROJECT_PLAN §4.1, lesson 3). Mixing errors into result
data means a real column named `error` is indistinguishable from a failure.

The distinction that matters most is `is_query_fault`: a broken query is worth
asking the model to fix, whereas an unreachable database is not — retrying that
with a different query just burns attempts.
"""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for anything that goes wrong talking to a data source."""

    is_query_fault: bool = False

    def __init__(self, message: str, *, original: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.original = original


class ConnectionFailed(ConnectorError):
    """Could not reach the database, or credentials were rejected."""


class SchemaIntrospectionError(ConnectorError):
    """Connected, but could not read the schema."""


class QueryExecutionError(ConnectorError):
    """The database rejected the query — bad syntax, unknown column, bad cast.

    Worth feeding back to the model: this is the repairable case.
    """

    is_query_fault = True


class QueryTimeout(ConnectorError):
    """The query ran longer than allowed and was cancelled.

    Repairable in principle — the usual fix is to aggregate or narrow the date
    range rather than to rewrite the logic.
    """

    is_query_fault = True


class UnsupportedDataSource(ConnectorError):
    """No connector exists for the requested type."""
