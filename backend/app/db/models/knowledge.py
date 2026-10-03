"""The retrieval index: one row per retrievable unit of analytics context."""

from __future__ import annotations

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, uuid_pk

EMBEDDING_DIM = 1024


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_source_kind", "data_source_id", "kind"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_knowledge_chunks_tsv", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24))
    """table | definition | example_query | doc"""
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    """Link back to the catalog row a `table` chunk was built from."""

    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    """The text that is embedded, and the text shown to the model."""
    content_hash: Mapped[str] = mapped_column(String(64))
    """Skip re-embedding when a chunk's content has not changed."""
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    """Structured fields: `sql_expression` for definitions, `question`/`sql` for examples."""
    source: Mapped[str] = mapped_column(String(16), default="auto")
    """auto (built from the catalog) | seed | user"""

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """Which model produced `embedding`. Vectors from different models are not
    comparable, so this is how a model change finds the rows to re-embed."""
    # Unused until hybrid retrieval, but cheaper to create now than to migrate later.
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', title || ' ' || content)", persisted=True),
        nullable=True,
    )
