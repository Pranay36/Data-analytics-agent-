"""Loading what a run needs to know before any model is asked anything."""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import CatalogTable, DataSource
from app.graph.deps import GraphDeps
from app.graph.drilldown import DimensionInfo
from app.graph.persistence import set_stage
from app.graph.state import AnalysisState, DatasourceContext, RunError
from app.rag import RetrievedContext, retrieve
from app.rag.embeddings import EmbeddingError
from app.rag.indexer import build_table_drafts
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

FALLBACK_TABLES = 6


def _as_of(source: DataSource) -> date:
    """The date "today" means for this data.

    Fixed per data source rather than read from the clock: the demo data ends on a
    set date, and "last month" must give the same answer whenever the evaluation
    is run.
    """
    raw = (source.business_context or {}).get("as_of_date")
    try:
        return date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return date.today()


async def load_context(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "loading_context")

    async with deps.sessionmaker() as session:
        source = await session.get(DataSource, uuid.UUID(state["datasource_id"]))
        if source is None:
            return {
                "stop_reason": "error",
                "error": RunError(code="DATASOURCE_NOT_FOUND", message="Data source not found."),
            }

        tables = (
            await session.scalars(
                select(CatalogTable)
                .where(
                    CatalogTable.data_source_id == source.id,
                    CatalogTable.is_active.is_(True),
                    CatalogTable.is_queryable.is_(True),
                )
                .options(selectinload(CatalogTable.columns))
            )
        ).all()

    if not tables:
        return {
            "stop_reason": "no_context",
            "error": RunError(
                code="NOT_SYNCED",
                message="This data source has no discovered tables. Sync it first.",
            ),
        }

    return {
        "datasource": DatasourceContext(
            id=str(source.id),
            name=source.name,
            type=source.type,
            dialect=deps.connector.dialect,
            as_of_date=_as_of(source),
            currency=(source.business_context or {}).get("currency"),
        ),
        "allowed_tables": [table.qualified_name for table in tables],
        "known_columns": {
            table.qualified_name: [column.name for column in table.columns] for table in tables
        },
        "dimensions": [
            DimensionInfo(
                table=table.table_name, column=column.name,
                sample_values=[str(v) for v in (column.sample_values or [])],
                description=(column.description or None),
            )
            for table in tables
            for column in table.columns
            if column.is_dimension
        ],
        "drilldown_depth": 0,
        "filter_path": [],
        "used_dimensions": [],
        "drill": None,
        "mode": "primary",
        "current_question": state["user_question"],
        "attempt": 1,
        "last_error": None,
        "stop_reason": None,
    }


async def retrieve_context(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "retrieving_context")
    source_id = uuid.UUID(state["datasource_id"])
    settings = deps.settings

    drill = state.get("drill")
    must_include = [drill.dimension.split(".")[0]] if drill is not None else None

    try:
        async with deps.sessionmaker() as session:
            context = await retrieve(
                session,
                source_id,
                state["current_question"],
                deps.embeddings,
                must_include=must_include,
                top_k_tables=settings.rag_top_k_tables,
                top_k_definitions=settings.rag_top_k_definitions,
                top_k_examples=settings.rag_top_k_examples,
                min_similarity=settings.rag_min_similarity,
                relative_cutoff=settings.rag_relative_cutoff,
                max_tables=settings.rag_max_tables,
            )
    except EmbeddingError as exc:
        # Retrieval being down must not take the whole analysis with it. Fall back
        # to the catalog's own tables, and say plainly that they are unranked.
        logger.warning("retrieval unavailable, using catalog fallback", extra={"error": str(exc)})
        context = await _catalog_fallback(deps, source_id, state["current_question"])

    # A follow-up step keeps what the earlier steps were given. The metric's own table
    # and definitions are still needed, and may not be what the narrower question's
    # wording would retrieve on its own.
    if (previous := state.get("retrieved")) is not None and state.get("mode") == "drilldown":
        context = _merge(previous, context)

    return {"retrieved": context}


def _merge(previous: RetrievedContext, new: RetrievedContext) -> RetrievedContext:
    def union(old: list[RetrievedChunk], fresh: list[RetrievedChunk]) -> list[RetrievedChunk]:
        merged = {chunk.title: chunk for chunk in old}
        for chunk in fresh:
            if chunk.title not in merged or chunk.score > merged[chunk.title].score:
                merged[chunk.title] = chunk
        return sorted(merged.values(), key=lambda chunk: -chunk.score)

    joins = {join.render(): join for join in [*previous.joins, *new.joins]}
    return RetrievedContext(
        question=new.question,
        tables=union(previous.tables, new.tables),
        definitions=union(previous.definitions, new.definitions),
        examples=union(previous.examples, new.examples),
        joins=list(joins.values()),
        confidence=new.confidence,
    )


async def _catalog_fallback(
    deps: GraphDeps, source_id: uuid.UUID, question: str
) -> RetrievedContext:
    async with deps.sessionmaker() as session:
        drafts = await build_table_drafts(session, source_id)

    return RetrievedContext(
        question=question,
        tables=[
            RetrievedChunk(
                kind="table", title=draft.title, content=draft.content,
                payload=draft.payload, score=0.0, reason="fallback",
            )
            for draft in drafts[:FALLBACK_TABLES]
        ],
        confidence="low",
    )
