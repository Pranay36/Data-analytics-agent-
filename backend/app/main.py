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

    yield

    logger.info("shutting down InsightFlow")


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
