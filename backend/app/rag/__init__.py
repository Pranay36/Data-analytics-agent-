"""Retrieval of analytics context: schema, business definitions and verified SQL."""

from app.rag.chunks import ChunkDraft, build_table_chunk
from app.rag.context import render_context
from app.rag.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    FakeEmbeddingProvider,
    build_embedding_provider,
)
from app.rag.indexer import IndexResult, index_chunks, load_seed_knowledge, reindex_data_source
from app.rag.retriever import RetrievedChunk, RetrievedContext, retrieve

__all__ = [
    "ChunkDraft",
    "EmbeddingError",
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "IndexResult",
    "RetrievedChunk",
    "RetrievedContext",
    "build_embedding_provider",
    "build_table_chunk",
    "index_chunks",
    "load_seed_knowledge",
    "reindex_data_source",
    "render_context",
    "retrieve",
]
