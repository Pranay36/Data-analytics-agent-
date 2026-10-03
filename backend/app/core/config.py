"""Application settings.

Every tunable lives here and is sourced from the environment (or a local `.env`),
so that nothing operational is hardcoded in application code. In particular, LLM
model ids are configuration rather than code: free-tier model availability changes
frequently, and swapping a model must never require a code change
(see PROJECT_PLAN §17.6).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: Literal["local", "demo", "test"] = "local"
    demo_mode: bool = False
    """Public-demo hardening: disables custom DB connections and evaluation runs."""

    # `NoDecode` stops pydantic-settings from JSON-decoding the raw env value, so
    # the `_split_csv` validator below can accept plain comma-separated strings.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # ── Databases ────────────────────────────────────────────────────────────
    database_url: str = ""
    """Our own metadata DB (system plane). SQLAlchemy async URL."""

    demo_analytics_url: str = ""
    """Seeded 'customer' analytical DB used by the demo datasource (analytical plane)."""

    # ── Storage ──────────────────────────────────────────────────────────────
    upload_dir: Path = REPO_DIR / "data" / "uploads"
    max_upload_mb: int = 20

    # ── Secrets ──────────────────────────────────────────────────────────────
    datasource_encryption_key: SecretStr = SecretStr("")
    """Fernet key for datasource passwords. Required outside `local`."""

    # ── LLM providers (see PROJECT_PLAN §17.6) ───────────────────────────────
    llm_fallback_chain: Annotated[list[str], NoDecode] = []
    """Overrides `llm.chain` in models.yaml. Empty means use that file."""
    """Ordered `provider:model` entries, tried in sequence. The default is the set
    verified to return correct, guard-approved SQL in both structured-output modes.
    Free-tier availability changes often, so override via LLM_FALLBACK_CHAIN.

    Avoid `openrouter/free`: it picks a model per request, so behaviour is not
    reproducible (observed: plain text instead of JSON, and a 39s response)."""

    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    groq_api_key: SecretStr = SecretStr("")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_base_url: str = "http://localhost:11434/v1"

    llm_model_query: str = ""
    llm_model_analysis: str = ""
    llm_model_visualization: str = ""
    """Per-agent model override. Empty means "use the head of the fallback chain"."""

    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_max_concurrency: int = 2
    """Semaphore width. Free tiers have low per-minute limits."""

    # ── Quota protection (see PROJECT_PLAN §17.6.1) ──────────────────────────
    llm_cache_enabled: bool = True
    llm_cache_dir: Path = BACKEND_DIR / ".llm_cache"
    llm_cache_mode: Literal["off", "read_write", "record"] = "read_write"

    # ── Embeddings ───────────────────────────────────────────────────────────
    embedding_model: str = ""
    """Overrides `embeddings.default` in models.yaml, as `provider:model`."""

    embedding_dim: int | None = None
    """Overrides the dimension declared in models.yaml. Must match the stored
    vector column, so changing it needs a migration and a full re-index."""
    embedding_cache_enabled: bool = True
    embedding_cache_dir: Path = BACKEND_DIR / ".embedding_cache"
    """Must match `knowledge_chunks.embedding`. Vectors from different models are
    not comparable, so changing the model means re-embedding everything.

    Why this model: free, 1024-d (fits pgvector's 2000-d HNSW limit), and it put
    the right table in the top 3 for 10 of 10 test questions. nemotron-3-embed-1b
    scored marginally higher but only offers 2048 dimensions."""

    # ── RAG ──────────────────────────────────────────────────────────────────
    rag_top_k_tables: int = 5
    rag_top_k_definitions: int = 4
    rag_top_k_examples: int = 3
    rag_min_similarity: float = 0.05
    """Absolute floor, kept low on purpose: it is a sanity guard against a
    completely unrelated match, not the main filter. Different embedding models
    produce wildly different score ranges — the model in use scores a correct
    table around 0.2 — so the real filtering is `rag_relative_cutoff`."""

    rag_relative_cutoff: float = 0.55
    """Keep tables scoring at least this fraction of the best match. Relative
    because it adapts to whatever range a model produces, where an absolute
    threshold has to be re-tuned for every model."""

    rag_max_tables: int = 8
    """Hard cap after expansion, so a well-connected schema cannot drag the whole
    catalog into the prompt."""
    rag_hybrid_enabled: bool = False  # P1; the `search_tsv` column exists from day 1

    # ── SQL safety (see PROJECT_PLAN §14) ────────────────────────────────────
    sql_max_rows: int = 500
    sql_timeout_seconds: int = 20

    # ── Agent budgets (see PROJECT_PLAN §15.5) ───────────────────────────────
    max_drilldown_depth: int = 3
    max_llm_calls_per_analysis: int = 12
    max_tokens_per_analysis: int = 80_000
    max_sql_repair_attempts: int = 2
    viz_agent_enabled: bool = True
    """Set false during development: the deterministic dashboard builder takes over,
    cutting roughly a third of LLM calls per analysis."""

    @field_validator("cors_origins", "llm_fallback_chain", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated strings from .env for list-typed settings."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env and set it to your "
                "Postgres (pgvector) connection string."
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
