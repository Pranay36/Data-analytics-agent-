"""Turning text into vectors.

Behind an interface for the same reason the LLM is: the hosted free model we use
today may be withdrawn, and swapping it must not touch the retriever or the
indexer. The dimension is part of the contract — vectors from different models
are not comparable, so changing a model means re-embedding everything.

`embed_documents` and `embed_query` are separate methods even though they do the
same thing today. Many retrieval models are *asymmetric*: they expect a short
question and a long passage to be encoded differently. Keeping the two apart now
means adopting such a model later is a change inside this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import struct
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)

# Free endpoints are rate-limited and occasionally slow; keep batches modest and
# retry a few times rather than failing a whole catalog sync.
_MAX_BATCH = 16
_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = 2.0


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimension: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed text that will be stored and searched over."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a question being searched with."""


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Any `/embeddings` endpoint following the OpenAI shape."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        client: httpx.AsyncClient | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self.name = name
        self.model = model
        self.dimension = dimension
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._sleep = sleep

    async def _post(self, texts: list[str]) -> list[list[float]]:
        client = self._client or httpx.AsyncClient(timeout=120.0)
        try:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = await client.post(
                        f"{self._base_url}/embeddings",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={"model": self.model, "input": texts},
                    )
                except httpx.HTTPError as exc:
                    if attempt == _MAX_ATTEMPTS - 1:
                        raise EmbeddingError(f"{self.name}: {exc}") from exc
                    await self._sleep(_BACKOFF_SECONDS * (2**attempt))
                    continue

                if response.status_code == 200:
                    data = response.json()["data"]
                    # The API does not promise ordering; `index` does.
                    ordered = sorted(data, key=lambda item: item.get("index", 0))
                    return [item["embedding"] for item in ordered]

                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == _MAX_ATTEMPTS - 1:
                    raise EmbeddingError(
                        f"{self.name}: HTTP {response.status_code} {response.text[:200]}"
                    )

                wait = _BACKOFF_SECONDS * (2**attempt)
                if (header := response.headers.get("retry-after")) is not None:
                    with contextlib.suppress(ValueError):
                        wait = max(wait, float(header))
                logger.info("embedding retry", extra={"wait": wait, "status": response.status_code})
                await self._sleep(wait)
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            if self._client is None:
                await client.aclose()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = texts[start : start + _MAX_BATCH]
            embedded = await self._post(batch)
            if len(embedded) != len(batch):
                raise EmbeddingError(
                    f"{self.name}: asked for {len(batch)} vectors, received {len(embedded)}"
                )
            for vector in embedded:
                if len(vector) != self.dimension:
                    # Would be stored in a column of fixed width, so fail loudly
                    # rather than corrupt the index.
                    raise EmbeddingError(
                        f"{self.name}: model returned {len(vector)} dimensions, "
                        f"expected {self.dimension}. Update EMBEDDING_DIM and re-index."
                    )
            vectors.extend(embedded)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic vectors from text, for tests that must not call a network.

    Not semantic: it hashes word tokens into buckets, so texts sharing words come
    out closer than texts that do not. Enough to exercise indexing, storage and
    ranking mechanics, but never a substitute for measuring real retrieval
    quality — that is what `app.scripts.eval_retrieval` is for.
    """

    name = "fake"
    model = "fake-embedding"

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode()).digest()
            bucket = struct.unpack("<I", digest[:4])[0] % self.dimension
            vector[bucket] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector] if norm else vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def build_embedding_provider(settings=None) -> EmbeddingProvider:
    from app.core.config import get_settings

    settings = settings or get_settings()
    providers = {
        "openrouter": (settings.openrouter_base_url, settings.openrouter_api_key),
        "groq": (settings.groq_base_url, settings.groq_api_key),
        "gemini": (settings.gemini_base_url, settings.gemini_api_key),
    }
    try:
        base_url, api_key = providers[settings.embedding_provider]
    except KeyError:
        raise EmbeddingError(
            f"Unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}; "
            f"expected one of {', '.join(providers)}"
        ) from None

    secret = api_key.get_secret_value()
    if not secret:
        raise EmbeddingError(
            f"No API key for embedding provider {settings.embedding_provider!r}."
        )

    provider: EmbeddingProvider = OpenAICompatibleEmbeddingProvider(
        name=settings.embedding_provider,
        base_url=base_url,
        api_key=secret,
        model=settings.embedding_model,
        dimension=settings.embedding_dim,
    )

    if settings.embedding_cache_enabled:
        from app.rag.cache import CachedEmbeddingProvider

        provider = CachedEmbeddingProvider(provider, settings.embedding_cache_dir)
    return provider
