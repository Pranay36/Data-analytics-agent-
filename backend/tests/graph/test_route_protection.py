"""Every route is behind a sign-in unless it is on a short, named list.

Walks the app's own OpenAPI description and calls each route with no credentials, so it
checks what actually happens rather than how the code is arranged. A route added next month
to a protected router is covered without anyone touching this file; a route added to a *new*
router, and forgotten, fails here.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.main import create_app

pytestmark = pytest.mark.integration

# The routes that must work before anyone is signed in. Adding to this list is a decision
# that deserves a review, which is the point of keeping it here.
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}


@pytest.fixture
async def anonymous(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-signing-key-" + "k" * 40)
    get_settings.cache_clear()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.openapi = create_app().openapi()  # type: ignore[attr-defined]
        yield client
    get_settings.cache_clear()


def _routes(openapi: dict) -> list[tuple[str, str]]:
    return [
        (method.upper(), path)
        for path, methods in openapi["paths"].items()
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def test_the_walk_actually_finds_the_routes() -> None:
    """Guards the guard: an empty route list would make the test below pass for nothing."""
    routes = _routes(create_app().openapi())
    assert ("POST", "/api/v1/analyses") in routes
    assert ("GET", "/api/v1/datasources") in routes
    assert len(routes) >= 15


async def test_every_route_outside_the_public_list_refuses_an_anonymous_caller(anonymous) -> None:
    unprotected = []
    for method, path in _routes(anonymous.openapi):
        if (method, path) in PUBLIC:
            continue
        concrete = path.replace("{data_source_id}", str(uuid.uuid4())).replace(
            "{analysis_id}", str(uuid.uuid4())
        )
        response = await anonymous.request(method, concrete)
        if response.status_code != 401:
            unprotected.append(f"{method} {path} -> {response.status_code}")

    assert not unprotected, "reachable without signing in:\n  " + "\n  ".join(unprotected)


async def test_a_garbage_token_is_treated_like_no_token(anonymous) -> None:
    response = await anonymous.get(
        "/api/v1/analyses", headers={"Authorization": "Bearer not.a.real.token"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_a_refresh_token_is_not_accepted_as_a_bearer_token(anonymous) -> None:
    from app.core.security import create_refresh_token

    token, _, _ = create_refresh_token(uuid.uuid4())
    response = await anonymous.get(
        "/api/v1/analyses", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
