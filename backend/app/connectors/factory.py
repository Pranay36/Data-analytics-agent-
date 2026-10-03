"""Resolve a data source type to its connector.

The only place in the codebase that maps a type string to a class. Everything
else depends on `DataConnector`, which is what makes adding ClickHouse a new
registry entry rather than an edit spread across the agent and graph layers.
"""

from __future__ import annotations

from typing import Any

from app.connectors.base import DataConnector
from app.connectors.duckdb_connector import DuckDBConnector
from app.connectors.errors import UnsupportedDataSource
from app.connectors.postgres import PostgresConnector

# Imported lazily in future entries where the driver is heavy; both of these
# are already dependencies, so a direct import is simpler.
_REGISTRY: dict[str, type[DataConnector]] = {
    PostgresConnector.engine: PostgresConnector,
    DuckDBConnector.engine: DuckDBConnector,
    # "csv" is how a data source created from an upload is labelled; it is
    # queried through DuckDB like any other.
    "csv": DuckDBConnector,
}

SUPPORTED_TYPES = tuple(sorted(_REGISTRY))


def create_connector(
    source_type: str, config: dict[str, Any], secret: str | None = None
) -> DataConnector:
    """Build a connector for `source_type`.

    Args:
        source_type: `postgres`, `duckdb` or `csv`.
        config: non-secret connection settings.
        secret: decrypted password, where the engine needs one.

    Raises:
        UnsupportedDataSource: no connector is registered for that type.
    """
    try:
        connector_class = _REGISTRY[source_type.lower()]
    except KeyError:
        raise UnsupportedDataSource(
            f"No connector for {source_type!r}. Supported: {', '.join(SUPPORTED_TYPES)}."
        ) from None

    return connector_class(config, secret)
