"""On-disk cache for embeddings.

Embeddings are deterministic for a given (model, text), so there is no reason to
pay for the same vector twice. This matters more than the LLM cache: a free-tier
embedding endpoint may allow only tens of calls a day, and re-running the
retrieval evaluation alone asks for twenty-odd query vectors.

Keyed by model as well as text, because vectors from different models are not
interchangeable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.rag.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class CachedEmbeddingProvider(EmbeddingProvider):
    """Wraps another provider, serving repeats from disk.

    A miss list is built first so that one batch call covers every uncached text,
    rather than one call per text.
    """

    def __init__(self, inner: EmbeddingProvider, directory: Path, *, enabled: bool = True) -> None:
        self.inner = inner
        self.name = f"cached:{inner.name}"
        self.model = inner.model
        self.dimension = inner.dimension
        self.directory = directory
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _read(self, text: str) -> list[float] | None:
        if not self.enabled:
            return None
        try:
            return json.loads((self.directory / f"{_key(self.model, text)}.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("unreadable embedding cache entry", extra={"error": str(exc)})
            return None

    def _write(self, text: str, vector: list[float]) -> None:
        if not self.enabled:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / f"{_key(self.model, text)}.json").write_text(json.dumps(vector))
        except OSError as exc:  # a cache failure must never fail a request
            logger.warning("could not write embedding cache entry", extra={"error": str(exc)})

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float] | None] = [self._read(text) for text in texts]
        missing = [index for index, vector in enumerate(vectors) if vector is None]
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)

        if missing:
            fetched = await self.inner.embed_documents([texts[index] for index in missing])
            for index, vector in zip(missing, fetched, strict=True):
                vectors[index] = vector
                self._write(texts[index], vector)

        return [vector for vector in vectors if vector is not None]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]
