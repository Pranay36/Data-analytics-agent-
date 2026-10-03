"""Use cases for data sources.

Owns the rules the HTTP layer and scripts both rely on: a connection is tested
*before* it is saved (so a typo never leaves a dead row behind), secrets are
encrypted on the way in and decrypted only to build a connector, and a connector
is always closed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog.sync import sync_catalog
from app.connectors import (
    ConnectionFailed,
    ConnectionTestResult,
    DataConnector,
    create_connector,
)
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import CatalogTable, DataSource
from app.schemas.datasource import SyncResult

logger = logging.getLogger(__name__)


class DataSourceNotFound(LookupError):
    pass


class DataSourceNameTaken(ValueError):
    pass


async def test_connection(
    source_type: str, config: dict[str, Any], password: str | None
) -> ConnectionTestResult:
    """Check credentials without saving anything."""
    connector = create_connector(source_type, config, password)
    try:
        return await asyncio.to_thread(connector.test_connection)
    finally:
        await asyncio.to_thread(connector.close)


async def create_data_source(
    session: AsyncSession,
    *,
    name: str,
    source_type: str,
    config: dict[str, Any],
    password: str | None = None,
    business_context: dict[str, Any] | None = None,
    sync: bool = True,
) -> DataSource:
    """Test, save, and (by default) discover the schema of a new data source.

    Raises:
        DataSourceNameTaken: the name is in use.
        ConnectionFailed: the connection test failed; nothing was saved.
    """
    if await session.scalar(select(DataSource.id).where(DataSource.name == name)):
        raise DataSourceNameTaken(f"A data source named {name!r} already exists.")

    result = await test_connection(source_type, config, password)
    if not result.ok:
        raise ConnectionFailed(result.error or "Could not connect.")

    source = DataSource(
        name=name,
        type=source_type,
        config=config,
        encrypted_secret=encrypt_secret(password) if password else None,
        business_context=business_context or {},
        status="connected",
    )
    session.add(source)
    await session.flush()

    if sync:
        await sync_data_source(session, source.id)
    return source


@asynccontextmanager
async def open_connector(source: DataSource) -> AsyncIterator[DataConnector]:
    """The only place a stored secret is decrypted.

    The plaintext lives inside the connector for the duration of the block and
    is not copied anywhere else — in particular never into graph state, which is
    persisted and logged.
    """
    secret = decrypt_secret(source.encrypted_secret) if source.encrypted_secret else None
    connector = create_connector(source.type, source.config, secret)
    try:
        yield connector
    finally:
        await asyncio.to_thread(connector.close)


async def sync_data_source(session: AsyncSession, data_source_id: uuid.UUID) -> SyncResult:
    source = await get_data_source(session, data_source_id)
    source.status = "syncing"
    await session.flush()

    try:
        async with open_connector(source) as connector:
            result = await sync_catalog(session, source.id, connector)
    except Exception as exc:
        # Keep whatever catalog we had; a failed sync should not erase knowledge.
        source.status = "error"
        source.status_message = str(exc)[:500]
        await session.flush()
        raise

    source.status = "connected"
    source.status_message = None
    source.last_synced_at = datetime.now(UTC)
    await session.flush()
    return result


async def get_data_source(session: AsyncSession, data_source_id: uuid.UUID) -> DataSource:
    source = await session.get(DataSource, data_source_id)
    if source is None:
        raise DataSourceNotFound(str(data_source_id))
    return source


async def list_data_sources(session: AsyncSession) -> list[tuple[DataSource, int]]:
    """Each source with its count of active tables."""
    counts = (
        select(CatalogTable.data_source_id, func.count().label("n"))
        .where(CatalogTable.is_active.is_(True))
        .group_by(CatalogTable.data_source_id)
        .subquery()
    )
    rows = await session.execute(
        select(DataSource, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.data_source_id == DataSource.id)
        .order_by(DataSource.created_at)
    )
    return [(source, int(count)) for source, count in rows.all()]


async def get_schema(session: AsyncSession, data_source_id: uuid.UUID) -> list[CatalogTable]:
    await get_data_source(session, data_source_id)
    return list(
        (
            await session.scalars(
                select(CatalogTable)
                .where(
                    CatalogTable.data_source_id == data_source_id,
                    CatalogTable.is_active.is_(True),
                )
                .options(selectinload(CatalogTable.columns))
                .order_by(CatalogTable.schema_name, CatalogTable.table_name)
            )
        ).all()
    )


async def delete_data_source(session: AsyncSession, data_source_id: uuid.UUID) -> None:
    await session.delete(await get_data_source(session, data_source_id))
    await session.flush()
