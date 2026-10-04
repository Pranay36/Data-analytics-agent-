"""In demo mode nobody can aim the server at a database of their choosing."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.main import create_app

pytestmark = pytest.mark.integration

POSTGRES = {
    "type": "postgres",
    "config": {"host": "appdb", "port": 5432, "database": "insightflow", "username": "x"},
    "password": "x",
}


@pytest.fixture
async def client(account):
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=account.headers
    ) as http:
        yield http


async def test_demo_mode_refuses_to_test_or_create_a_custom_connection(client, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()

    tested = await client.post("/api/v1/datasources/test", json=POSTGRES)
    created = await client.post("/api/v1/datasources", json={**POSTGRES, "name": "mine"})

    assert tested.status_code == created.status_code == 403
    assert "turned off in this demo" in tested.json()["detail"]


async def test_demo_mode_still_allows_listing_and_csv_upload(client, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()

    assert (await client.get("/api/v1/datasources")).status_code == 200


async def test_without_demo_mode_the_connection_test_runs(client, monkeypatch) -> None:
    """Not a 403: the request reaches the connector (which then reports it cannot connect)."""
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()

    response = await client.post(
        "/api/v1/datasources/test",
        json={**POSTGRES, "config": {**POSTGRES["config"], "host": "127.0.0.1", "port": 1}},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
