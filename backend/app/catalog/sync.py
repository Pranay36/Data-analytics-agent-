"""Discover a data source's schema and store it in our own database.

The one rule that shapes this module: **re-syncing must never destroy human
work.** A table that vanishes upstream is soft-deleted, not removed, and a
description someone edited by hand is never overwritten by what the database
says. Without that, every sync would silently undo the curation that makes
retrieval good.

Rows are matched on their natural key (schema + table, table + column) rather
than recreated, so their ids — and anything that references them — stay stable.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import DataConnector, SchemaSnapshot
from app.connectors.types import ColumnProfile, TableInfo
from app.db.models import CatalogColumn, CatalogTable, TableRelationship
from app.schemas.datasource import SyncResult

logger = logging.getLogger(__name__)

# A column is a drill-down candidate when it is a low-cardinality label.
DIMENSION_TYPES = {"string", "boolean"}


def _is_dimension(normalized_type: str, profile: ColumnProfile | None) -> bool:
    return bool(
        profile is not None
        and normalized_type in DIMENSION_TYPES
        and profile.is_low_cardinality
    )


async def sync_catalog(
    session: AsyncSession, data_source_id: uuid.UUID, connector: DataConnector
) -> SyncResult:
    started = time.perf_counter()

    # Connectors are synchronous; keep the event loop free while they work.
    snapshot: SchemaSnapshot = await asyncio.to_thread(connector.introspect_schema)

    existing_tables = {
        table.qualified_name: table
        for table in (
            await session.scalars(
                select(CatalogTable).where(CatalogTable.data_source_id == data_source_id)
            )
        ).all()
    }

    seen: set[str] = set()
    column_total = 0

    for info in snapshot.tables:
        seen.add(info.qualified_name)
        table = existing_tables.get(info.qualified_name)

        if table is None:
            table = CatalogTable(
                data_source_id=data_source_id,
                schema_name=info.schema_name,
                table_name=info.table_name,
                description_source="db",
            )
            session.add(table)
            await session.flush()
            columns_by_name: dict[str, CatalogColumn] = {}
        else:
            existing_columns = await session.scalars(
                select(CatalogColumn).where(CatalogColumn.table_id == table.id)
            )
            columns_by_name = {column.name: column for column in existing_columns}

        table.object_type = info.object_type
        table.row_count_estimate = info.row_count_estimate
        table.is_active = True
        if table.description_source != "user":
            table.description = info.description

        profiles = await _profile(connector, info)
        column_total += _merge_columns(session, table, info, columns_by_name, profiles)

    # Gone upstream: keep the row, and the knowledge attached to it.
    for qualified, table in existing_tables.items():
        if qualified not in seen:
            table.is_active = False

    relationships = await _sync_relationships(session, data_source_id, snapshot)
    await session.flush()

    return SyncResult(
        tables=len(snapshot.tables),
        columns=column_total,
        relationships=relationships,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def _profile(connector: DataConnector, info: TableInfo) -> dict[str, ColumnProfile]:
    """Sample only the columns worth sampling.

    Numeric and temporal columns have no useful "sample values", and profiling
    every column of a wide table would make registering a source needlessly slow.
    """
    candidates = [c.name for c in info.columns if c.normalized_type in DIMENSION_TYPES]
    if not candidates:
        return {}
    try:
        return await asyncio.to_thread(
            connector.profile_columns, info.schema_name, info.table_name, candidates
        )
    except Exception as exc:  # noqa: BLE001 - best effort by contract
        logger.warning(
            "profiling failed",
            extra={"table": info.qualified_name, "error": str(exc)},
        )
        return {}


def _merge_columns(
    session: AsyncSession,
    table: CatalogTable,
    info: TableInfo,
    existing: dict[str, CatalogColumn],
    profiles: dict[str, ColumnProfile],
) -> int:
    for ordinal, column_info in enumerate(info.columns):
        column = existing.get(column_info.name)
        if column is None:
            column = CatalogColumn(
                table_id=table.id, name=column_info.name, description_source="db"
            )
            session.add(column)

        column.data_type = column_info.data_type
        column.normalized_type = column_info.normalized_type
        column.nullable = column_info.nullable
        column.ordinal = ordinal
        if column.description_source != "user":
            column.description = column_info.description

        profile = profiles.get(column_info.name)
        if profile is not None:
            column.distinct_count = profile.distinct_count
            column.null_fraction = profile.null_fraction
            column.sample_values = profile.sample_values or None
        column.is_dimension = _is_dimension(column_info.normalized_type, profile)

    return len(info.columns)


async def _sync_relationships(
    session: AsyncSession, data_source_id: uuid.UUID, snapshot: SchemaSnapshot
) -> int:
    """Import foreign keys, leaving hand-declared relationships untouched."""
    existing = {
        (r.from_table, r.from_column, r.to_table, r.to_column): r
        for r in (
            await session.scalars(
                select(TableRelationship).where(
                    TableRelationship.data_source_id == data_source_id
                )
            )
        ).all()
    }

    current: set[tuple[str, str, str, str]] = set()
    for fk in snapshot.foreign_keys:
        key = (fk.from_table, fk.from_column, fk.to_table, fk.to_column)
        current.add(key)
        if key not in existing:
            session.add(
                TableRelationship(
                    data_source_id=data_source_id,
                    from_table=fk.from_table,
                    from_column=fk.from_column,
                    to_table=fk.to_table,
                    to_column=fk.to_column,
                    source="fk",
                )
            )

    # A foreign key that was dropped upstream goes; one a user declared stays.
    for key, relationship in existing.items():
        if relationship.source == "fk" and key not in current:
            await session.delete(relationship)

    return len(current)
