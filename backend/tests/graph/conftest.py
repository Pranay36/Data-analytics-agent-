"""Shared setup for graph tests: the real graph, a real database, a scripted model."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DataSource
from app.db.models.knowledge import EMBEDDING_DIM
from app.db.session import get_engine, get_sessionmaker
from app.llm import ChainEntry, LLMClient
from app.llm.fake import FakeLLMProvider
from app.rag import FakeEmbeddingProvider, reindex_data_source
from app.services import datasource_service as service

DEMO = {"host": "localhost", "port": 5433, "database": "shopsphere",
        "username": "insightflow_ro", "schemas": ["public"]}


def reply(**fields) -> str:
    """A scripted Query Agent reply."""
    base = {"can_answer": True, "explanation": "Computes it."}
    return json.dumps({**base, **fields})


@pytest.fixture
async def source(monkeypatch):
    """A synced, indexed data source, removed again afterwards."""
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    monkeypatch.setenv("DATASOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # The workflow tests script the Query and Analysis agents. The Visualization Agent is
    # off here so they need not also script a dashboard reply; the rule-built dashboard
    # still runs, and test_dashboard_workflow turns the agent on.
    monkeypatch.setenv("VIZ_AGENT_ENABLED", "false")
    get_settings.cache_clear()

    maker = get_sessionmaker()
    try:
        async with maker() as session:
            created = await service.create_data_source(
                session, name="graph-test", source_type="postgres",
                config=DEMO, password="insightflow_ro",
                business_context={"as_of_date": "2026-06-30", "currency": "INR"},
            )
            await reindex_data_source(session, created.id, FakeEmbeddingProvider(EMBEDDING_DIM))
            await session.commit()
            source_id = created.id
    except OSError:
        pytest.skip("Application database unreachable")

    yield source_id

    async with maker() as session:
        row = await session.scalar(select(DataSource).where(DataSource.id == source_id))
        if row is not None:
            await session.delete(row)
            await session.commit()
    await get_engine().dispose()
    get_settings.cache_clear()


def scripted_llm(*replies) -> tuple[LLMClient, FakeLLMProvider]:
    provider = FakeLLMProvider(list(replies))
    client = LLMClient(
        {"fake": provider}, [ChainEntry(provider="fake", model="m")],
        recorder=None, max_retries=0,
    )
    return client, provider


EMBEDDINGS = FakeEmbeddingProvider(EMBEDDING_DIM)

REVENUE_SQL = (
    "SELECT SUM(total_amount) AS revenue FROM orders WHERE status = 'SUCCESS' "
    "AND order_date >= DATE '2026-05-01' AND order_date < DATE '2026-06-01'"
)


def analysis(**fields) -> str:
    """A scripted Analysis Agent reply. By default: summarise, do not drill down."""
    base = {"summary": "Revenue was as expected.", "findings": [], "needs_drilldown": False,
            "confidence": "high", "caveats": []}
    return json.dumps({**base, **fields})
