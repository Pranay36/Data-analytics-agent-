"""Everything a node needs that is not state.

Nodes are plain functions of `(state) -> partial state`. Their collaborators —
the LLM client, the embedding provider, a way to reach the databases — arrive
here instead, bound once when the graph is built. That keeps nodes testable with
fakes and keeps unserialisable objects out of state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors import DataConnector
from app.core.config import Settings
from app.llm import CallBudget, LLMClient
from app.rag.embeddings import EmbeddingProvider


@dataclass
class GraphDeps:
    settings: Settings
    llm: LLMClient
    embeddings: EmbeddingProvider
    sessionmaker: async_sessionmaker[AsyncSession]
    connector: DataConnector
    budget: CallBudget
    llm_calls: int = 0
    tokens: int = 0
    stages: list[str] = field(default_factory=list)

    @property
    def max_attempts(self) -> int:
        """One try plus the allowed repairs."""
        return 1 + self.settings.max_sql_repair_attempts
