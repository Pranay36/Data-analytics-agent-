"""Building the text that gets embedded.

This file decides retrieval quality more than any other. A question is matched
against these strings, so they have to read like the thing someone would ask
about — business language, not just identifiers. `shipping_region` alone is a
poor match for "which region lost sales"; the same column with its description
and its actual values (`North`, `South`, …) is a good one.

Three kinds of chunk, each solving a different failure:

* **table** — stops invented tables and columns, and wrong literal values.
* **definition** — stops SQL that is valid but computes the wrong thing. Nothing
  in a schema says revenue excludes cancelled orders.
* **example_query** — gives a proven pattern to copy, embedded on the *question*
  so it matches how people ask rather than how SQL is written.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# Wide tables would otherwise dilute the embedding: a question matches the
# overall text, so a hundred column names drown out the table's purpose.
MAX_COLUMNS_IN_CHUNK = 40
MAX_SAMPLE_VALUES = 8


@dataclass
class ChunkDraft:
    """A chunk before it is embedded or stored."""

    kind: str
    title: str
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    ref_id: Any | None = None
    source: str = "auto"

    @property
    def content_hash(self) -> str:
        """Identifies unchanged content, so re-syncing does not re-embed everything."""
        return hashlib.sha256(f"{self.kind}|{self.title}|{self.content}".encode()).hexdigest()


def _column_line(column: Any) -> str:
    """One column, with whatever makes it findable and usable."""
    parts = [f"- {column.name} ({column.normalized_type})"]
    if column.description:
        parts.append(f": {column.description}")

    # Actual values are the single most useful thing here: they stop the model
    # guessing status = 'completed' when the data says 'SUCCESS'.
    if column.sample_values:
        values = [str(value) for value in column.sample_values[:MAX_SAMPLE_VALUES]]
        parts.append(f" [values: {', '.join(values)}]")
    return "".join(parts)


def _ordered_columns(columns: list[Any]) -> list[Any]:
    """Keep the most informative columns when a table is too wide to include whole."""
    if len(columns) <= MAX_COLUMNS_IN_CHUNK:
        return columns
    ranked = sorted(
        columns,
        key=lambda column: (
            bool(column.description) or bool(column.sample_values),
            column.is_dimension,
        ),
        reverse=True,
    )
    return ranked[:MAX_COLUMNS_IN_CHUNK]


def build_table_chunk(table: Any, related_tables: list[str] | None = None) -> ChunkDraft:
    """Render a catalog table as searchable text.

    Args:
        table: a `CatalogTable` with its `columns` loaded.
        related_tables: tables joinable to this one, so a question spanning two
            tables can surface either.
    """
    lines = [f"Table: {table.qualified_name}"]
    if table.description:
        lines.append(table.description)
    if table.row_count_estimate:
        lines.append(f"Approximately {table.row_count_estimate:,} rows.")

    columns = _ordered_columns(list(table.columns))
    if columns:
        lines.append("Columns:")
        lines.extend(_column_line(column) for column in columns)
    if len(table.columns) > len(columns):
        lines.append(f"...and {len(table.columns) - len(columns)} more columns.")

    if related_tables:
        lines.append(f"Joins to: {', '.join(sorted(related_tables))}")

    return ChunkDraft(
        kind="table",
        title=table.qualified_name,
        content="\n".join(lines),
        ref_id=table.id,
        payload={
            "schema_name": table.schema_name,
            "table_name": table.table_name,
            "columns": [column.name for column in table.columns],
        },
    )


def build_definition_chunk(definition: dict[str, Any]) -> ChunkDraft:
    """Render a business definition.

    Synonyms are embedded with the definition on purpose: a user asking about
    "GMV" should reach the chunk titled "Revenue".
    """
    name = definition["name"]
    lines = [f"Definition: {name}"]

    if synonyms := definition.get("synonyms"):
        lines.append(f"Also called: {', '.join(synonyms)}")
    if meaning := definition.get("meaning"):
        lines.append(meaning)
    if sql := definition.get("sql"):
        lines.append(f"Computed as: {sql}")
    if date_column := definition.get("date_column"):
        lines.append(f"Date column for time filters: {date_column}")
    if tables := definition.get("tables"):
        lines.append(f"Uses tables: {', '.join(tables)}")
    if notes := definition.get("notes"):
        lines.append(f"Note: {notes}")

    return ChunkDraft(
        kind="definition",
        title=name,
        content="\n".join(lines),
        source="seed",
        payload={
            "sql": definition.get("sql"),
            "tables": definition.get("tables", []),
            "date_column": definition.get("date_column"),
            "synonyms": definition.get("synonyms", []),
        },
    )


def build_example_query_chunk(example: dict[str, Any]) -> ChunkDraft:
    """Render a verified question-and-SQL pair.

    The embedded text is deliberately the *question*, lightly annotated. Matching
    a user's question against stored SQL would compare a sentence with a block of
    syntax; matching it against a past question compares like with like.
    """
    question = example["question"]
    lines = [f"Example question: {question}"]
    if notes := example.get("notes"):
        lines.append(notes)
    if tables := example.get("tables"):
        lines.append(f"Uses tables: {', '.join(tables)}")

    return ChunkDraft(
        kind="example_query",
        title=question,
        content="\n".join(lines),
        source="seed",
        payload={"question": question, "sql": example["sql"], "tables": example.get("tables", [])},
    )
