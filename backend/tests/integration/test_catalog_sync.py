"""Catalog sync against the live demo database.

Runs in a transaction that is rolled back, so it never leaves rows in the
application database.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import CatalogColumn, CatalogTable, DataSource, TableRelationship
from app.services import datasource_service as service

pytestmark = pytest.mark.integration

DEMO_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "shopsphere",
    "username": "insightflow_ro",
    "schemas": ["public"],
}


@pytest.fixture
async def session(monkeypatch):
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    monkeypatch.setenv("DATASOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            async with async_sessionmaker(conn, expire_on_commit=False)() as db:
                yield db
            await transaction.rollback()
    except OSError:
        pytest.skip("Application database unreachable")
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def _orders(session: AsyncSession, source: DataSource) -> CatalogTable:
    tables = await service.get_schema(session, source.id)
    return next(t for t in tables if t.table_name == "orders")


async def _create(session: AsyncSession) -> DataSource:
    return await service.create_data_source(
        session,
        name="test-shopsphere",
        source_type="postgres",
        config=DEMO_CONFIG,
        password="insightflow_ro",
    )


async def test_create_discovers_the_schema(session) -> None:
    source = await _create(session)

    assert source.status == "connected"
    tables = {t.table_name for t in await service.get_schema(session, source.id)}
    core = {"customers", "products", "orders", "order_items", "payments", "refunds"}
    assert core <= tables
    # The schema also carries deliberately confusable tables, so that retrieval
    # quality can be measured rather than assumed.
    assert "orders_legacy" in tables


async def test_secret_is_stored_encrypted(session) -> None:
    source = await _create(session)

    assert source.has_secret
    assert source.encrypted_secret != "insightflow_ro"
    assert "insightflow_ro" not in source.encrypted_secret
    assert "password" not in source.config


async def test_reads_database_comments_into_descriptions(session) -> None:
    """The schema's comments are what later tell the model status != revenue."""
    source = await _create(session)
    orders = await _orders(session, source)

    assert "failed or were cancelled" in orders.description
    status = next(c for c in orders.columns if c.name == "status")
    assert "Only SUCCESS counts as revenue" in status.description


async def test_samples_low_cardinality_columns(session) -> None:
    source = await _create(session)
    orders = await _orders(session, source)
    by_name = {c.name: c for c in orders.columns}

    assert set(by_name["status"].sample_values) == {"SUCCESS", "FAILED", "CANCELLED", "PENDING"}
    assert by_name["status"].is_dimension
    assert by_name["shipping_region"].is_dimension
    # A number is not a label, however few distinct values it happens to have.
    assert not by_name["total_amount"].is_dimension
    assert by_name["total_amount"].sample_values is None


async def test_imports_foreign_keys_as_join_paths(session) -> None:
    source = await _create(session)
    rows = (
        await session.scalars(
            select(TableRelationship).where(TableRelationship.data_source_id == source.id)
        )
    ).all()
    joins = {(r.from_table, r.from_column, r.to_table) for r in rows}

    assert ("public.orders", "customer_id", "public.customers") in joins
    assert ("public.order_items", "order_id", "public.orders") in joins


async def test_resync_never_overwrites_a_human_edited_description(session) -> None:
    """The rule that makes curation worth doing: sync must not undo it."""
    source = await _create(session)
    orders = await _orders(session, source)
    status = next(c for c in orders.columns if c.name == "status")

    orders.description = "Revenue source of truth."
    orders.description_source = "user"
    status.description = "Hand-written note."
    status.description_source = "user"
    original_ids = (orders.id, status.id)
    await session.flush()

    await service.sync_data_source(session, source.id)

    refreshed = await session.scalar(select(CatalogTable).where(CatalogTable.id == orders.id))
    refreshed_status = await session.scalar(
        select(CatalogColumn).where(CatalogColumn.id == status.id)
    )
    assert refreshed.description == "Revenue source of truth."
    assert refreshed_status.description == "Hand-written note."
    assert (refreshed.id, refreshed_status.id) == original_ids, "ids must stay stable"


async def test_resync_is_idempotent(session) -> None:
    source = await _create(session)
    before = len(await service.get_schema(session, source.id))
    await service.sync_data_source(session, source.id)
    await service.sync_data_source(session, source.id)

    assert len(await service.get_schema(session, source.id)) == before
    columns = (await session.scalars(select(CatalogColumn))).all()
    pairs = [(c.table_id, c.name) for c in columns]
    assert len(pairs) == len(set(pairs)), "re-sync must not duplicate columns"


async def test_a_table_removed_upstream_is_soft_deleted(session) -> None:
    source = await _create(session)
    ghost = CatalogTable(
        data_source_id=source.id, schema_name="public", table_name="ghost_table",
        description="Curated knowledge that must survive.", description_source="user",
    )
    session.add(ghost)
    await session.flush()

    await service.sync_data_source(session, source.id)
    await session.refresh(ghost)

    assert ghost.is_active is False
    assert ghost.description == "Curated knowledge that must survive."
    assert "ghost_table" not in {t.table_name for t in await service.get_schema(session, source.id)}


async def test_a_failed_connection_saves_nothing(session) -> None:
    from app.connectors import ConnectionFailed

    with pytest.raises(ConnectionFailed):
        await service.create_data_source(
            session, name="bad", source_type="postgres",
            config=DEMO_CONFIG, password="wrong-password",
        )
    assert await session.scalar(select(DataSource).where(DataSource.name == "bad")) is None


async def test_duplicate_names_are_rejected(session) -> None:
    await _create(session)
    with pytest.raises(service.DataSourceNameTaken):
        await _create(session)
