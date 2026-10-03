"""Keeping the search index in step with the catalog.

Re-indexing is cheap only because unchanged chunks are skipped: each chunk
carries a hash of its text, so a sync that discovers nothing new embeds nothing.
That matters on a free-tier embedding endpoint, where every call is rationed.

A chunk is also re-embedded when the *model* changes, because vectors from
different models cannot be compared — a mixed index silently returns nonsense
rather than failing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import CatalogTable, KnowledgeChunk, TableRelationship
from app.rag.chunks import (
    ChunkDraft,
    build_definition_chunk,
    build_example_query_chunk,
    build_table_chunk,
)
from app.rag.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    total: int = 0
    embedded: int = 0
    """Chunks that needed a new vector. The rest were unchanged."""
    deleted: int = 0

    @property
    def skipped(self) -> int:
        return self.total - self.embedded


def load_seed_knowledge(path: Path) -> list[ChunkDraft]:
    """Read definitions and example queries from a YAML file."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    drafts = [build_definition_chunk(item) for item in data.get("definitions", [])]
    drafts += [build_example_query_chunk(item) for item in data.get("example_queries", [])]
    return drafts


async def build_table_drafts(session: AsyncSession, data_source_id: uuid.UUID) -> list[ChunkDraft]:
    """One chunk per queryable table, annotated with what it joins to."""
    tables = (
        await session.scalars(
            select(CatalogTable)
            .where(
                CatalogTable.data_source_id == data_source_id,
                CatalogTable.is_active.is_(True),
                CatalogTable.is_queryable.is_(True),
            )
            .options(selectinload(CatalogTable.columns))
            .order_by(CatalogTable.schema_name, CatalogTable.table_name)
        )
    ).all()

    relationships = (
        await session.scalars(
            select(TableRelationship).where(TableRelationship.data_source_id == data_source_id)
        )
    ).all()

    # Join edges run both ways for retrieval: a question about refunds should be
    # able to surface products, and the reverse.
    neighbours: dict[str, set[str]] = {}
    for relationship in relationships:
        left = relationship.from_table.split(".")[-1]
        right = relationship.to_table.split(".")[-1]
        neighbours.setdefault(left, set()).add(right)
        neighbours.setdefault(right, set()).add(left)

    return [
        build_table_chunk(table, sorted(neighbours.get(table.table_name, set())))
        for table in tables
    ]


async def index_chunks(
    session: AsyncSession,
    data_source_id: uuid.UUID,
    drafts: list[ChunkDraft],
    provider: EmbeddingProvider,
    *,
    prune: bool = True,
) -> IndexResult:
    """Store `drafts`, embedding only those that are new or changed.

    Args:
        prune: remove stored chunks that no longer appear in `drafts`. Off when
            indexing one kind at a time, which would otherwise delete the others.
    """
    existing = {
        (chunk.kind, chunk.title): chunk
        for chunk in (
            await session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.data_source_id == data_source_id)
            )
        ).all()
    }

    result = IndexResult(total=len(drafts))
    stale: list[tuple[ChunkDraft, KnowledgeChunk]] = []

    for draft in drafts:
        chunk = existing.get((draft.kind, draft.title))
        if chunk is None:
            chunk = KnowledgeChunk(
                data_source_id=data_source_id, kind=draft.kind, title=draft.title
            )
            session.add(chunk)

        unchanged = (
            chunk.content_hash == draft.content_hash
            and chunk.embedding is not None
            and chunk.embedding_model == provider.model
        )
        chunk.content = draft.content
        chunk.content_hash = draft.content_hash
        chunk.payload = draft.payload
        chunk.ref_id = draft.ref_id
        chunk.source = draft.source
        if not unchanged:
            stale.append((draft, chunk))

    if stale:
        vectors = await provider.embed_documents([draft.content for draft, _ in stale])
        for (_, chunk), vector in zip(stale, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = provider.model
        result.embedded = len(stale)

    if prune:
        wanted = {(draft.kind, draft.title) for draft in drafts}
        for key, chunk in existing.items():
            if key not in wanted:
                await session.delete(chunk)
                result.deleted += 1

    await session.flush()
    logger.info(
        "indexed knowledge",
        extra={"embedded": result.embedded, "skipped": result.skipped, "deleted": result.deleted},
    )
    return result


async def reindex_data_source(
    session: AsyncSession,
    data_source_id: uuid.UUID,
    provider: EmbeddingProvider,
    *,
    seed_path: Path | None = None,
) -> IndexResult:
    """Rebuild the whole index for one data source."""
    drafts = await build_table_drafts(session, data_source_id)
    if seed_path is not None:
        drafts += load_seed_knowledge(seed_path)
    return await index_chunks(session, data_source_id, drafts, provider)


def summarise(result: IndexResult) -> dict[str, Any]:
    return {
        "chunks": result.total,
        "embedded": result.embedded,
        "skipped": result.skipped,
        "deleted": result.deleted,
    }
