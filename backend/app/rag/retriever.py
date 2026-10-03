"""Finding the right context for a question.

Vector search alone is not enough, and measurement showed why. Asked "what was
our GMV for the year?", the embedding model ranked `orders` *eighth* of fourteen
tables — yet it ranked the **Revenue** definition first, and that definition
names `orders` as the table it uses. Following that link is certain; hoping the
table ranking was right is not.

So retrieval runs in three steps:

1. **Vector search**, filtered by a *relative* cutoff. Absolute thresholds do not
   survive a change of model: the one in use scores a correct table around 0.2,
   where another might score 0.7. Keeping whatever scores near the best match
   adapts automatically.
2. **Follow definitions and examples** to the tables they name.
3. **Bridge the gaps**, adding a table only where it is needed to join two
   tables already chosen — `refunds` and `products` are only joinable through
   `order_items`.

Step 3 is deliberately narrow. Adding every neighbour of every chosen table
pulled in almost the whole schema, because `orders` touches five other tables:
eight tables in the prompt where one was needed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk, TableRelationship
from app.rag.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]


class RetrievedChunk(BaseModel):
    kind: str
    title: str
    content: str
    payload: dict = Field(default_factory=dict)
    score: float
    """Cosine similarity, 0 to 1."""
    reason: str = "vector"
    """How it was chosen: `vector`, `definition_link` or `join_bridge`. Shown in
    the debug panel, and used by evaluation to separate ranking quality from the
    rescue steps that follow it."""


class Join(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str

    def render(self) -> str:
        return f"{self.from_table}.{self.from_column} = {self.to_table}.{self.to_column}"


class RetrievedContext(BaseModel):
    question: str
    tables: list[RetrievedChunk] = Field(default_factory=list)
    definitions: list[RetrievedChunk] = Field(default_factory=list)
    examples: list[RetrievedChunk] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    confidence: Confidence = "high"

    @property
    def table_names(self) -> list[str]:
        return [chunk.title for chunk in self.tables]

    @property
    def all_chunks(self) -> list[RetrievedChunk]:
        return [*self.tables, *self.definitions, *self.examples]

    def debug_payload(self) -> dict:
        """Stored on the analysis so a user can see what the model was given."""
        return {
            "question": self.question,
            "confidence": self.confidence,
            "chunks": [
                {"kind": c.kind, "title": c.title, "score": round(c.score, 4), "reason": c.reason}
                for c in self.all_chunks
            ],
            "joins": [join.render() for join in self.joins],
        }


def _bare(name: str) -> str:
    return name.split(".")[-1].lower()


async def _search(
    session: AsyncSession,
    data_source_id: uuid.UUID,
    vector: list[float],
    kind: str,
    limit: int,
) -> list[RetrievedChunk]:
    distance = KnowledgeChunk.embedding.cosine_distance(vector).label("distance")
    rows = (
        await session.execute(
            select(KnowledgeChunk, distance)
            .where(
                KnowledgeChunk.data_source_id == data_source_id,
                KnowledgeChunk.kind == kind,
                KnowledgeChunk.embedding.isnot(None),
            )
            .order_by(distance)
            .limit(limit)
        )
    ).all()

    return [
        RetrievedChunk(
            kind=chunk.kind,
            title=chunk.title,
            content=chunk.content,
            payload=chunk.payload or {},
            score=max(0.0, 1.0 - float(dist)),
        )
        for chunk, dist in rows
    ]


def _above_cutoff(
    chunks: list[RetrievedChunk], *, limit: int, min_similarity: float, relative_cutoff: float
) -> list[RetrievedChunk]:
    """Keep what scores near the best match, and clears a low absolute floor."""
    if not chunks:
        return []
    threshold = max(min_similarity, chunks[0].score * relative_cutoff)
    return [chunk for chunk in chunks if chunk.score >= threshold][:limit]


def _bridges(
    selected: set[str], edges: list[tuple[str, str]], candidates: set[str]
) -> set[str]:
    """Tables that connect two already-selected tables.

    `refunds` and `products` cannot be joined directly; `order_items` sits
    between them. Only such connectors are added — not every neighbour, which
    would pull in the whole schema through a central table like `orders`.
    """
    neighbours: dict[str, set[str]] = {}
    for left, right in edges:
        neighbours.setdefault(left, set()).add(right)
        neighbours.setdefault(right, set()).add(left)

    directly_linked = {frozenset(edge) for edge in edges}
    needed: set[str] = set()

    for table in sorted(selected):
        for other in sorted(selected):
            if table >= other or frozenset({table, other}) in directly_linked:
                continue
            shared = neighbours.get(table, set()) & neighbours.get(other, set())
            needed |= shared & candidates - selected

    return needed


async def retrieve(
    session: AsyncSession,
    data_source_id: uuid.UUID,
    question: str,
    provider: EmbeddingProvider,
    *,
    top_k_tables: int = 5,
    top_k_definitions: int = 4,
    top_k_examples: int = 3,
    min_similarity: float = 0.05,
    relative_cutoff: float = 0.55,
    max_tables: int = 8,
) -> RetrievedContext:
    """Gather the tables, definitions, examples and joins a question needs."""
    vector = await provider.embed_query(question)

    # Over-fetch, then filter: the cutoff should remove weak matches, not limit
    # how many strong ones can be considered.
    all_tables = await _search(session, data_source_id, vector, "table", top_k_tables * 3)
    definitions = await _search(
        session, data_source_id, vector, "definition", top_k_definitions * 2
    )
    examples = await _search(session, data_source_id, vector, "example_query", top_k_examples * 2)

    cutoff = {"min_similarity": min_similarity, "relative_cutoff": relative_cutoff}
    tables = _above_cutoff(all_tables, limit=top_k_tables, **cutoff)
    definitions = _above_cutoff(definitions, limit=top_k_definitions, **cutoff)
    examples = _above_cutoff(examples, limit=top_k_examples, **cutoff)

    confidence: Confidence = "high"
    if not tables:
        # Nothing cleared the floor at all. The closest few are still better than
        # an empty prompt, but the agent is warned not to trust them.
        tables = all_tables[:top_k_tables]
        confidence = "low"
    elif all_tables and all_tables[0].score < min_similarity * 2:
        confidence = "medium"

    selected = {chunk.title: chunk for chunk in tables}
    by_bare_name = {_bare(chunk.title): chunk for chunk in all_tables}

    def pull_in(name: str, reason: str) -> None:
        candidate = by_bare_name.get(_bare(name))
        if candidate is not None and candidate.title not in selected:
            selected[candidate.title] = candidate.model_copy(update={"reason": reason})

    # Step 2: tables named by the definitions and examples we retrieved. High
    # precision, because a definition states which tables it is computed from.
    for chunk in [*definitions, *examples]:
        for name in chunk.payload.get("tables", []):
            pull_in(name, "definition_link")

    relationships = (
        await session.scalars(
            select(TableRelationship).where(TableRelationship.data_source_id == data_source_id)
        )
    ).all()
    edges = [(_bare(r.from_table), _bare(r.to_table)) for r in relationships]

    # Step 3: connectors only.
    for name in _bridges(
        {_bare(title) for title in selected}, edges, set(by_bare_name)
    ):
        pull_in(name, "join_bridge")

    ordered = sorted(selected.values(), key=lambda chunk: -chunk.score)[:max_tables]

    # Joins between the tables we ended up with, so the model is not left to
    # guess which columns pair up.
    final = {_bare(chunk.title) for chunk in ordered}
    joins = [
        Join(
            from_table=r.from_table,
            from_column=r.from_column,
            to_table=r.to_table,
            to_column=r.to_column,
        )
        for r in relationships
        if _bare(r.from_table) in final and _bare(r.to_table) in final
    ]

    logger.info(
        "retrieved context",
        extra={
            "question": question[:120],
            "tables": [chunk.title for chunk in ordered],
            "confidence": confidence,
        },
    )

    return RetrievedContext(
        question=question,
        tables=ordered,
        definitions=definitions,
        examples=examples,
        joins=joins,
        confidence=confidence,
    )
