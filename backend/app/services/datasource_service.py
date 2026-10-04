"""Use cases for data sources.

Owns the rules the HTTP layer and scripts both rely on: a connection is tested
*before* it is saved (so a typo never leaves a dead row behind), secrets are
encrypted on the way in and decrypted only to build a connector, and a connector
is always closed.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
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
from app.connectors.csv_import import CsvImportError, build_duckdb_from_csvs
from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.models import CatalogTable, DataSource, KnowledgeChunk
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


# Test seam: tests supply fake embeddings rather than calling a real provider.
embedding_override: dict = {"provider": None}


class InvalidUpload(ValueError):
    pass


async def index_data_source(session: AsyncSession, data_source_id: uuid.UUID) -> int:
    """Make a source's tables searchable. Returns how many chunks were embedded.

    Best-effort: a source that cannot be indexed right now (embedding quota spent) is
    still registered and queryable, and retrieval falls back to the catalog's own tables.
    Existing definitions and examples are left alone (`prune=False`), since this only
    describes tables.
    """
    from app.rag import build_embedding_provider
    from app.rag.embeddings import EmbeddingError
    from app.rag.indexer import build_table_drafts, index_chunks

    try:
        provider = embedding_override["provider"] or build_embedding_provider()
        drafts = await build_table_drafts(session, data_source_id)
        result = await index_chunks(session, data_source_id, drafts, provider, prune=False)
        return result.embedded
    except EmbeddingError as exc:
        logger.warning("could not index data source", extra={"error": str(exc)})
        return 0


async def create_csv_data_source(
    session: AsyncSession, *, name: str, files: list[tuple[str, bytes]]
) -> DataSource:
    """Turn uploaded CSV files into a queryable data source.

    The files become tables in a DuckDB file, so uploaded data goes through exactly the
    same SQL pipeline as a database. The import is the only moment external file access
    is enabled; queries afterwards run with it switched off.
    """
    settings = get_settings()
    if not files:
        raise InvalidUpload("Upload at least one CSV file.")
    if await session.scalar(select(DataSource.id).where(DataSource.name == name)):
        raise DataSourceNameTaken(f"A data source named {name!r} already exists.")

    limit = settings.max_upload_mb * 1024 * 1024
    for filename, content in files:
        if not filename.lower().endswith(".csv"):
            raise InvalidUpload(f"{filename!r} is not a .csv file.")
        if len(content) > limit:
            raise InvalidUpload(f"{filename!r} is larger than {settings.max_upload_mb} MB.")

    folder = settings.upload_dir / str(uuid.uuid4())
    raw = folder / "raw"
    raw.mkdir(parents=True)
    try:
        paths = []
        for filename, content in files:
            # Only the base name is used: a client-supplied path must never choose where
            # a file lands.
            path = raw / filename.replace("\\", "/").split("/")[-1]
            path.write_bytes(content)
            paths.append(path)

        target = folder / "data.duckdb"
        await asyncio.to_thread(build_duckdb_from_csvs, paths, target)
    except CsvImportError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise InvalidUpload(exc.message) from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    source = DataSource(
        name=name,
        type="csv",
        config={"path": str(target)},
        status="connected",
        business_context={},
    )
    session.add(source)
    await session.flush()
    await sync_data_source(session, source.id)
    return source


async def example_questions(
    session: AsyncSession, data_source_id: uuid.UUID, limit: int = 6
) -> list[str]:
    """Verified questions for this source, to suggest in the UI."""
    await get_data_source(session, data_source_id)
    rows = await session.scalars(
        select(KnowledgeChunk.title)
        .where(
            KnowledgeChunk.data_source_id == data_source_id,
            KnowledgeChunk.kind == "example_query",
        )
        .order_by(KnowledgeChunk.title)
        .limit(limit)
    )
    return list(rows.all())
