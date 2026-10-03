"""Indexing and retrieval against a real database, using fake embeddings.

The fake provider hashes words into buckets, so texts sharing vocabulary come out
closer. That is enough to exercise storage, pruning and the expansion steps
deterministically and for free. It says nothing about real retrieval quality —
`app.scripts.eval_retrieval` measures that against the real model.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import KnowledgeChunk
from app.db.models.knowledge import EMBEDDING_DIM
from app.rag import FakeEmbeddingProvider, index_chunks
from app.rag.chunks import ChunkDraft
from app.rag.indexer import build_table_drafts, reindex_data_source
from app.rag.retriever import retrieve
from app.services import datasource_service as service

pytestmark = pytest.mark.integration

DEMO_CONFIG = {
    "host": "localhost", "port": 5433, "database": "shopsphere",
    "username": "insightflow_ro", "schemas": ["public"],
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


@pytest.fixture
async def source(session):
    return await service.create_data_source(
        session, name="rag-test", source_type="postgres",
        config=DEMO_CONFIG, password="insightflow_ro",
    )


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    # The stored column has a fixed width, so a fake must match it or every
    # insert fails on dimension rather than on anything being tested.
    return FakeEmbeddingProvider(EMBEDDING_DIM)


async def test_table_drafts_describe_the_catalog(session, source) -> None:
    drafts = await build_table_drafts(session, source.id)
    titles = {draft.title for draft in drafts}

    assert "public.orders" in titles
    orders = next(d for d in drafts if d.title == "public.orders")
    assert "SUCCESS" in orders.content, "sample values must reach the chunk"
    assert "customers" in orders.content, "joinable tables must be listed"


async def test_indexing_stores_vectors(session, source, provider) -> None:
    result = await reindex_data_source(session, source.id, provider)
    stored = (await session.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.data_source_id == source.id))).all()

    assert result.embedded == result.total > 0
    assert all(chunk.embedding is not None for chunk in stored)
    assert all(chunk.embedding_model == provider.model for chunk in stored)


async def test_reindexing_skips_unchanged_chunks(session, source, provider) -> None:
    """The behaviour that keeps a free-tier embedding quota viable."""
    first = await reindex_data_source(session, source.id, provider)
    second = await reindex_data_source(session, source.id, provider)

    assert second.embedded == 0
    assert second.skipped == first.total


async def test_changed_content_is_re_embedded(session, source, provider) -> None:
    await reindex_data_source(session, source.id, provider)
    tables = await service.get_schema(session, source.id)
    orders = next(t for t in tables if t.table_name == "orders")
    orders.description = "A completely different description."
    await session.flush()

    assert (await reindex_data_source(session, source.id, provider)).embedded == 1


async def test_changing_the_embedding_model_forces_a_rebuild(session, source, provider) -> None:
    """Vectors from different models are not comparable; a mixed index is silently wrong."""
    await reindex_data_source(session, source.id, provider)

    other = FakeEmbeddingProvider(EMBEDDING_DIM)
    other.model = "some-other-model"
    result = await reindex_data_source(session, source.id, other)

    assert result.embedded == result.total


async def test_removed_chunks_are_pruned(session, source, provider) -> None:
    await index_chunks(session, source.id, [
        ChunkDraft(kind="definition", title="Temporary", content="x"),
    ], provider)
    result = await index_chunks(session, source.id, [], provider)

    assert result.deleted == 1


async def test_retrieval_returns_tables_definitions_and_joins(session, source, provider) -> None:
    await index_chunks(session, source.id, [
        *await build_table_drafts(session, source.id),
        ChunkDraft(kind="definition", title="Revenue",
                   content="Definition: Revenue. Money from successful orders.",
                   payload={"tables": ["orders"]}),
    ], provider)

    context = await retrieve(session, source.id, "revenue from successful orders", provider)

    assert context.tables
    assert any(c.title == "Revenue" for c in context.definitions)
    assert "public.orders" in context.table_names


async def test_a_definition_rescues_a_table_the_ranking_missed(session, source, provider) -> None:
    """The measured failure this exists for: asked about "GMV", the real embedding
    model ranked `orders` eighth of fourteen tables, while ranking the Revenue
    definition first. Following the definition's stated table is what saves it.

    The question here uses invented words so that no table chunk can match it
    directly, plus terms matching `suppliers` so that something clears the cutoff
    and the low-confidence fallback does not fire instead.
    """
    await index_chunks(session, source.id, [
        *await build_table_drafts(session, source.id),
        ChunkDraft(kind="definition", title="Zephyr index",
                   content="Definition: Zephyr index. Quixotic fathom per widget.",
                   payload={"tables": ["orders"]}),
    ], provider)

    context = await retrieve(
        session, source.id, "zephyr quixotic fathom suppliers lead time", provider
    )

    vector_ranked = [c.title for c in context.tables if c.reason == "vector"]
    assert "public.orders" not in vector_ranked, "premise: the ranking alone misses it"

    orders = next((c for c in context.tables if c.title == "public.orders"), None)
    assert orders is not None, "the definition's table must still be pulled in"
    assert orders.reason == "definition_link"


async def test_retrieval_is_scoped_to_one_data_source(session, source, provider) -> None:
    other = await service.create_data_source(
        session, name="rag-test-other", source_type="postgres",
        config=DEMO_CONFIG, password="insightflow_ro",
    )
    await index_chunks(session, other.id, [
        ChunkDraft(kind="definition", title="Secret", content="Definition: Secret revenue rule."),
    ], provider)
    await index_chunks(session, source.id, await build_table_drafts(session, source.id), provider)

    context = await retrieve(session, source.id, "secret revenue rule", provider)
    assert "Secret" not in {c.title for c in context.definitions}


async def test_context_is_capped(session, source, provider) -> None:
    await index_chunks(session, source.id, await build_table_drafts(session, source.id), provider)
    context = await retrieve(
        session, source.id, "orders customers products", provider, max_tables=3
    )
    assert len(context.tables) <= 3
