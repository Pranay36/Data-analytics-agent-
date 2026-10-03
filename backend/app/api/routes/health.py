"""Liveness and readiness endpoints.

`/health` answers "is the process up?" and must never touch a dependency — it is
what a container orchestrator polls. `/health/ready` answers "can this instance
actually serve traffic?" and therefore does check dependencies.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict[str, Any]:
    settings: Settings = get_settings()
    checks: dict[str, str] = {}

    # Database. Imported lazily so the app still starts (and /health still
    # answers) when the DB layer is not yet wired up.
    try:
        from app.db.session import check_database

        checks["database"] = "ok" if await check_database() else "unavailable"
    except ImportError:
        checks["database"] = "not_configured"
    except Exception as exc:  # pragma: no cover - surfaced as status text
        checks["database"] = f"error: {type(exc).__name__}"

    from app.observability.rate_limits import get_rate_limit_tracker

    ready = all(value == "ok" for value in checks.values())
    payload: dict[str, Any] = {
        "status": "ready" if ready else "degraded",
        "env": settings.app_env,
        "checks": checks,
    }

    # Surfaced here because a spent free-tier allowance looks like "requests are
    # failing" everywhere else, with no indication of when it returns.
    if limits := get_rate_limit_tracker().snapshot():
        payload["rate_limits"] = limits

    try:
        from app.core.model_registry import get_registry

        registry = get_registry()
        payload["models"] = {
            "llm": [ref.key for ref in registry.chain],
            "embedding": registry.embedding.ref.key,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break the probe
        payload["models"] = {"error": str(exc)[:200]}

    return payload
