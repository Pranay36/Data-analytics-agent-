
from app.rag import FakeEmbeddingProvider
from app.rag.cache import CachedEmbeddingProvider


class CountingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def embed_documents(self, texts):
        self.calls += 1
        return await super().embed_documents(texts)


async def test_repeated_text_is_not_embedded_twice(tmp_path) -> None:
    inner = CountingProvider()
    cached = CachedEmbeddingProvider(inner, tmp_path)

    first = await cached.embed_query("revenue last month")
    second = await cached.embed_query("revenue last month")

    assert first == second
    assert inner.calls == 1
    assert (cached.hits, cached.misses) == (1, 1)


async def test_only_the_uncached_texts_are_fetched(tmp_path) -> None:
    """A partial hit must still make one batch call, not one call per miss."""
    inner = CountingProvider()
    cached = CachedEmbeddingProvider(inner, tmp_path)

    await cached.embed_documents(["a", "b"])
    vectors = await cached.embed_documents(["a", "b", "c", "d"])

    assert len(vectors) == 4
    assert inner.calls == 2


async def test_vectors_keep_their_order_after_a_partial_hit(tmp_path) -> None:
    inner = CountingProvider()
    cached = CachedEmbeddingProvider(inner, tmp_path)
    await cached.embed_documents(["b"])

    direct = await FakeEmbeddingProvider().embed_documents(["a", "b", "c"])
    mixed = await cached.embed_documents(["a", "b", "c"])

    assert mixed == direct


async def test_a_different_model_does_not_reuse_vectors(tmp_path) -> None:
    inner = CountingProvider()
    first = CachedEmbeddingProvider(inner, tmp_path)
    await first.embed_query("revenue")

    inner.model = "another-model"
    second = CachedEmbeddingProvider(inner, tmp_path)
    await second.embed_query("revenue")

    assert inner.calls == 2, "vectors from different models are not interchangeable"


async def test_a_disabled_cache_always_calls_through(tmp_path) -> None:
    inner = CountingProvider()
    cached = CachedEmbeddingProvider(inner, tmp_path, enabled=False)

    await cached.embed_query("x")
    await cached.embed_query("x")
    assert inner.calls == 2


async def test_an_unwritable_directory_does_not_break_embedding(tmp_path) -> None:
    inner = CountingProvider()
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    cached = CachedEmbeddingProvider(inner, blocked / "cache")

    assert len(await cached.embed_query("x")) == inner.dimension
