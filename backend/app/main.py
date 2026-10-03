"""FastAPI application factory.

Kept deliberately thin: it wires configuration, logging, CORS and routers, and
owns startup/shutdown. All behaviour lives in `app.services` and below, so the
HTTP layer stays replaceable and testable.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import datasources, health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    logger.info(
        "starting InsightFlow",
        extra={"env": settings.app_env, "demo_mode": settings.demo_mode},
    )

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    _check_embedding_dimension()

    yield

    logger.info("shutting down InsightFlow")


def _check_embedding_dimension() -> None:
    """Refuse to start if the chosen embedding model does not fit the column.

    Caught here, the message names the problem. Caught at insert time, it
    surfaces as an opaque "expected 1024 dimensions, not 768" from the driver,
    halfway through indexing.
    """
    from app.core.model_registry import RegistryError, get_registry
    from app.db.models.knowledge import EMBEDDING_DIM

    try:
        embedding = get_registry().embedding
    except RegistryError as exc:
        raise RuntimeError(f"Invalid model configuration: {exc}") from exc

    if embedding.dimension != EMBEDDING_DIM:
        raise RuntimeError(
            f"Embedding model {embedding.ref.key!r} produces {embedding.dimension} "
            f"dimensions, but the knowledge_chunks.embedding column is "
            f"{EMBEDDING_DIM}. Either choose a {EMBEDDING_DIM}-dimension model in "
            f"models.yaml, or add a migration changing the column and re-index."
        )

    logger.info(
        "model configuration",
        extra={
            "llm_chain": [ref.key for ref in get_registry().chain],
            "embedding": embedding.ref.key,
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(json_output=not settings.is_local)

    app = FastAPI(
        title="InsightFlow",
        description="Agentic data analytics: a business question in, a dashboard out.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health lives at the root (not under the API prefix) so orchestrator probes
    # are unaffected by API versioning.
    app.include_router(health.router)
    app.include_router(datasources.router, prefix=API_PREFIX)

    return app


app = create_app()
