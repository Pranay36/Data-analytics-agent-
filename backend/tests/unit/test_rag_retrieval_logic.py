"""The parts of retrieval that are pure logic, tested without a database."""


from app.rag.retriever import RetrievedChunk, _above_cutoff, _bridges


def chunk(title: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(kind="table", title=title, content="", score=score)


def test_relative_cutoff_adapts_to_a_models_score_range() -> None:
    """One model scores a correct table ~0.2, another ~0.8. A fixed threshold
    cannot serve both; a fraction of the best match can."""
    low = [chunk("orders", 0.21), chunk("web_sessions", 0.20), chunk("suppliers", 0.04)]
    high = [chunk("orders", 0.84), chunk("web_sessions", 0.80), chunk("suppliers", 0.16)]

    for ranked in (low, high):
        kept = [c.title for c in _above_cutoff(
            ranked, limit=5, min_similarity=0.05, relative_cutoff=0.55)]
        assert kept == ["orders", "web_sessions"], "the far-behind match is dropped either way"


def test_absolute_floor_rejects_a_uniformly_weak_match() -> None:
    weak = [chunk("a", 0.03), chunk("b", 0.02)]
    assert _above_cutoff(weak, limit=5, min_similarity=0.05, relative_cutoff=0.55) == []


def test_cutoff_respects_the_limit() -> None:
    ranked = [chunk(f"t{i}", 0.9) for i in range(10)]
    assert len(_above_cutoff(ranked, limit=3, min_similarity=0.05, relative_cutoff=0.55)) == 3


EDGES = [
    ("orders", "customers"),
    ("order_items", "orders"),
    ("order_items", "products"),
    ("refunds", "orders"),
    ("refunds", "order_items"),
    ("payments", "orders"),
]
ALL = {"orders", "customers", "order_items", "products", "refunds", "payments"}


def test_a_connector_between_two_selected_tables_is_added() -> None:
    """refunds and products are only joinable through order_items."""
    assert _bridges({"refunds", "products"}, EDGES, ALL) == {"order_items"}


def test_neighbours_are_not_pulled_in_wholesale() -> None:
    """The bug this replaced: `orders` touches five tables, so expanding every
    neighbour put almost the whole schema into the prompt."""
    assert _bridges({"orders"}, EDGES, ALL) == set()


def test_directly_joinable_tables_need_no_connector() -> None:
    assert _bridges({"orders", "customers"}, EDGES, ALL) == set()


def test_connectors_are_limited_to_known_tables() -> None:
    assert _bridges({"refunds", "products"}, EDGES, {"refunds", "products"}) == set()
