"""What goes into a chunk decides what a question can match."""

from dataclasses import dataclass, field
from typing import Any

from app.rag.chunks import (
    MAX_COLUMNS_IN_CHUNK,
    build_definition_chunk,
    build_example_query_chunk,
    build_table_chunk,
)


@dataclass
class FakeColumn:
    name: str
    normalized_type: str = "string"
    description: str | None = None
    sample_values: list[Any] | None = None
    is_dimension: bool = False


@dataclass
class FakeTable:
    schema_name: str = "public"
    table_name: str = "orders"
    description: str | None = None
    row_count_estimate: int | None = None
    id: str = "t1"
    columns: list[FakeColumn] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


def test_table_chunk_includes_values_that_prevent_guessed_literals() -> None:
    """Knowing status is 'SUCCESS' and not 'completed' is what stops empty results."""
    table = FakeTable(
        description="One row per order placed.",
        columns=[
            FakeColumn("status", description="Order outcome.",
                       sample_values=["SUCCESS", "FAILED", "CANCELLED"], is_dimension=True),
        ],
    )
    content = build_table_chunk(table).content

    assert "public.orders" in content
    assert "One row per order placed." in content
    assert "SUCCESS" in content and "CANCELLED" in content
    assert "Order outcome." in content


def test_table_chunk_lists_joinable_tables() -> None:
    chunk = build_table_chunk(FakeTable(columns=[FakeColumn("id")]), ["customers", "refunds"])
    assert "Joins to: customers, refunds" in chunk.content


def test_wide_tables_are_truncated_so_the_embedding_is_not_diluted() -> None:
    columns = [FakeColumn(f"col_{i}") for i in range(60)]
    columns[59].description = "The important one."
    chunk = build_table_chunk(FakeTable(columns=columns))

    assert "and 20 more columns" in chunk.content
    assert "The important one." in chunk.content, "described columns must survive truncation"
    assert chunk.content.count("\n- ") == MAX_COLUMNS_IN_CHUNK


def test_definition_chunk_embeds_synonyms() -> None:
    """Someone asking about GMV must reach the chunk titled Revenue."""
    chunk = build_definition_chunk({
        "name": "Revenue", "synonyms": ["GMV", "turnover"],
        "meaning": "Money from completed orders.",
        "sql": "SUM(orders.total_amount) WHERE status = 'SUCCESS'",
        "tables": ["orders"],
    })

    assert chunk.title == "Revenue"
    assert "GMV" in chunk.content and "turnover" in chunk.content
    assert chunk.payload["tables"] == ["orders"], "the link retrieval follows"


def test_example_chunk_embeds_the_question_not_the_sql() -> None:
    """A user's question resembles a past question, not a block of SQL."""
    chunk = build_example_query_chunk({
        "question": "What was revenue last month?",
        "sql": "SELECT SUM(total_amount) FROM orders WHERE status = 'SUCCESS'",
        "tables": ["orders"],
    })

    assert "What was revenue last month?" in chunk.content
    assert "SELECT" not in chunk.content
    assert chunk.payload["sql"].startswith("SELECT")


def test_content_hash_changes_only_with_content() -> None:
    """This is what lets a re-index skip unchanged chunks and spend no quota."""
    first = build_table_chunk(FakeTable(columns=[FakeColumn("id")]))
    same = build_table_chunk(FakeTable(columns=[FakeColumn("id")]))
    different = build_table_chunk(FakeTable(description="now described",
                                            columns=[FakeColumn("id")]))

    assert first.content_hash == same.content_hash
    assert first.content_hash != different.content_hash
