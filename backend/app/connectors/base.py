"""The interface every data source implements.

This is the seam that keeps engine-specific code out of everything above it.
The agents, the graph and the catalog only ever see a `DataConnector`; adding
ClickHouse later means adding a subclass, not editing the layers above
(PROJECT_PLAN §9).

Connectors are **synchronous**. The drivers we use are sync (DuckDB has no async
API at all), and our concurrency is a handful of analyses, so callers run them
with `asyncio.to_thread` rather than us maintaining two code paths.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.connectors.types import (
    ColumnProfile,
    ConnectionTestResult,
    QueryResult,
    SchemaSnapshot,
)

logger = logging.getLogger(__name__)


class DataConnector(ABC):
    """Read-only access to one data source.

    Implementations must honour two guarantees, because the layers above rely on
    them and a lapse is a security problem rather than a bug:

    1. **Read-only.** Enforced at the connection itself, not by inspecting SQL.
       The SQL guard is a separate layer; this is the one underneath it.
    2. **Bounded.** Every query carries a timeout and a row cap.
    """

    engine: str
    """Identifier used by the factory and stored on the data source."""

    dialect: str
    """sqlglot dialect name. Also selects the prompt's dialect guidance, so the
    agent writes `toStartOfMonth` for ClickHouse and `date_trunc` elsewhere."""

    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        """
        Args:
            config: non-secret connection settings (host, port, database, …).
            secret: the password, decrypted, held only for this object's life.
                    Never logged, never placed into graph state.
        """
        self.config = config
        self._secret = secret

    # ── Required ─────────────────────────────────────────────────────────────
    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Check the source is reachable and the credentials work."""

    @abstractmethod
    def introspect_schema(self) -> SchemaSnapshot:
        """Discover tables, columns, types, comments and foreign keys."""

    @abstractmethod
    def execute(
        self, sql: str, *, max_rows: int = 500, timeout_seconds: int = 20
    ) -> QueryResult:
        """Run one read-only statement.

        Implementations fetch `max_rows + 1` rows so they can report
        `truncated` honestly rather than silently returning a partial answer.

        Raises:
            QueryExecutionError: the database rejected the statement.
            QueryTimeout: it exceeded `timeout_seconds`.
            ConnectionFailed: the source became unreachable.
        """

    @abstractmethod
    def profile_columns(
        self, schema_name: str, table_name: str, columns: list[str]
    ) -> dict[str, ColumnProfile]:
        """Sample values and distinct counts for the named columns.

        Best-effort by contract: profiling failures must return partial results
        rather than raise, because a missing sample degrades prompt quality but
        a raised error would block the whole catalog sync.
        """

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None:  # noqa: B027 - optional hook, not every engine holds state
        """Release the underlying connection. Safe to call more than once.

        Deliberately concrete rather than abstract: a connector over a stateless
        transport has nothing to close, and should not be forced to write `pass`.
        """

    def __enter__(self) -> DataConnector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        """Deliberately omits the secret: connectors surface in logs and tracebacks."""
        target = self.config.get("database") or self.config.get("path") or "?"
        return f"<{type(self).__name__} {target}>"
