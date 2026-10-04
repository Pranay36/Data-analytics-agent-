"""Running one analysis from question to stored result."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.logging import analysis_context
from app.db.models import Analysis
from app.db.session import get_sessionmaker
from app.graph import RECURSION_LIMIT, AnalysisState, GraphDeps, build_graph
from app.graph.persistence import finish_analysis
from app.llm import CallBudget, LLMClient
from app.rag import build_embedding_provider
from app.services import auth_service
from app.services import datasource_service as sources

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    analysis_id: str
    state: AnalysisState
    latency_ms: int
    llm_calls: int
    tokens: int


async def create_analysis(
    datasource_id: uuid.UUID, question: str, user_id: uuid.UUID
) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        analysis = Analysis(
            data_source_id=datasource_id, question=question, status="queued", user_id=user_id
        )
        session.add(analysis)
        await session.commit()
        return analysis.id


async def _owner(
    analysis_id: uuid.UUID | None, user_id: uuid.UUID | None
) -> uuid.UUID:
    """Who a run is charged to.

    A person's run arrives with `user_id`. Work nobody started (evaluation, the command-line
    tools) is charged to the system account, so it never eats into a real person's quota.
    """
    if user_id is not None:
        return user_id
    async with get_sessionmaker()() as session:
        if analysis_id is not None:
            owner = await session.scalar(select(Analysis.user_id).where(Analysis.id == analysis_id))
            if owner is not None:
                return owner
        system = await auth_service.ensure_system_user(session)
        await session.commit()
        return system.id


async def run_analysis(
    datasource_id: uuid.UUID,
    question: str,
    *,
    analysis_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    embeddings=None,
) -> RunResult:
    """Run the workflow and return its final state.

    `llm` and `embeddings` can be supplied, which is how tests run the real graph
    against scripted fakes.
    """
    settings = settings or get_settings()
    sessionmaker = get_sessionmaker()

    # Resolve the source first: creating the analysis row before checking would
    # turn an unknown id into an opaque foreign-key error instead of "not found".
    async with sessionmaker() as session:
        source = await sources.get_data_source(session, datasource_id)
    user_id = await _owner(analysis_id, user_id)
    analysis_id = analysis_id or await create_analysis(datasource_id, question, user_id)
    started = time.perf_counter()

    async with sources.open_connector(source) as connector:
        deps = GraphDeps(
            settings=settings,
            llm=llm or LLMClient.from_settings(settings),
            embeddings=embeddings or build_embedding_provider(settings),
            sessionmaker=sessionmaker,
            connector=connector,
            budget=CallBudget(
                max_calls=settings.max_llm_calls_per_analysis,
                max_tokens=settings.max_tokens_per_analysis,
            ),
        )

        with analysis_context(str(analysis_id), str(user_id)):
            try:
                state = await build_graph(deps).ainvoke(
                    {
                        "analysis_id": str(analysis_id),
                        "datasource_id": str(datasource_id),
                        "user_question": question,
                        "queries": [],
                    },
                    {"recursion_limit": RECURSION_LIMIT},
                )
            except Exception as exc:
                # A bug, not an expected outcome: record it so the run does not
                # sit as "running" forever, then let it propagate.
                logger.exception("analysis crashed")
                await finish_analysis(
                    deps, str(analysis_id),
                    {"status": "failed", "stage": "failed", "stop_reason": "error",
                     "error_code": "INTERNAL", "error_message": type(exc).__name__},
                )
                raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    async with sessionmaker() as session:
        analysis = await session.get(Analysis, analysis_id)
        if analysis is not None:
            analysis.latency_ms = latency_ms
            await session.commit()

    return RunResult(
        analysis_id=str(analysis_id), state=state, latency_ms=latency_ms,
        llm_calls=deps.budget.calls, tokens=deps.budget.total_tokens,
    )
