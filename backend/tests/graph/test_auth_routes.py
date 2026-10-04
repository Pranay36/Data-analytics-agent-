"""Sign-up, sign-in and session handling over HTTP, against a real database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.api.routes.auth import COOKIE_NAME
from app.core.config import get_settings
from app.db.models import RefreshToken, User
from app.db.session import get_engine, get_sessionmaker
from app.main import create_app

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery"


@pytest.fixture
async def client(monkeypatch):
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-signing-key-" + "k" * 40)
    get_settings.cache_clear()

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http

    async with get_sessionmaker()() as session:
        for user in await session.scalars(select(User).where(User.email.like("authtest-%"))):
            await session.delete(user)
        await session.commit()
    await get_engine().dispose()
    get_settings.cache_clear()


def email() -> str:
    return f"authtest-{uuid.uuid4().hex[:10]}@example.com"


async def register(client, address=None, password=PASSWORD):
    return await client.post(
        "/api/v1/auth/register", json={"email": address or email(), "password": password}
    )


def bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ── Registering and signing in ───────────────────────────────────────────────
async def test_registering_signs_you_in(client) -> None:
    response = await register(client)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["access_token"] and body["user"]["email"].startswith("authtest-")
    assert "hashed_password" not in response.text

    me = await client.get("/api/v1/auth/me", headers=bearer(response))
    assert me.status_code == 200
    assert me.json()["usage_today"]["llm_calls"] == 0


async def test_the_refresh_token_is_only_ever_an_httponly_cookie(client) -> None:
    response = await register(client)
    cookie = response.headers["set-cookie"].lower()

    assert COOKIE_NAME in cookie
    assert "httponly" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "refresh" not in response.json(), "a script on the page must not be able to read it"


async def test_an_email_can_only_register_once_whatever_its_case(client) -> None:
    address = email()
    assert (await register(client, address)).status_code == 201
    again = await register(client, address.upper())
    assert again.status_code == 409


async def test_a_short_password_is_refused(client) -> None:
    response = await register(client, password="short")
    assert response.status_code == 422


async def test_login_works_with_any_capitalisation_of_the_email(client) -> None:
    address = email()
    await register(client, address)

    response = await client.post(
        "/api/v1/auth/login", json={"email": address.upper(), "password": PASSWORD}
    )
    assert response.status_code == 200


async def test_a_wrong_password_and_an_unknown_email_look_identical(client) -> None:
    """Different answers would let anyone test which addresses have accounts."""
    address = email()
    await register(client, address)

    wrong = await client.post("/api/v1/auth/login", json={"email": address, "password": "nope"})
    unknown = await client.post("/api/v1/auth/login", json={"email": email(), "password": "nope"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_a_disabled_account_cannot_sign_in(client) -> None:
    address = email()
    await register(client, address)
    async with get_sessionmaker()() as session:
        await session.execute(update(User).where(User.email == address).values(is_active=False))
        await session.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"email": address, "password": PASSWORD}
    )
    assert response.status_code == 403


async def test_a_disabled_account_loses_access_with_a_token_it_already_holds(client) -> None:
    first = await register(client)
    async with get_sessionmaker()() as session:
        await session.execute(update(User).values(is_active=False).where(
            User.id == uuid.UUID(first.json()["user"]["id"])))
        await session.commit()

    assert (await client.get("/api/v1/auth/me", headers=bearer(first))).status_code == 403


async def test_registration_can_be_switched_off(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ALLOW_REGISTRATION", "false")
    get_settings.cache_clear()
    assert (await register(client)).status_code == 403


# ── Refresh tokens ───────────────────────────────────────────────────────────
async def test_refreshing_issues_a_new_session_and_retires_the_old_token(client) -> None:
    await register(client)
    old_cookie = client.cookies[COOKIE_NAME]

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 200, response.text
    assert client.cookies[COOKIE_NAME] != old_cookie, "the token rotates on every use"
    assert (await client.get("/api/v1/auth/me", headers=bearer(response))).status_code == 200


async def test_refreshing_without_a_cookie_is_refused(client) -> None:
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_replaying_an_already_used_refresh_token_ends_every_session(client) -> None:
    """A used token should never come back. If one does, a copy leaked: burn it all."""
    first = await register(client)
    stolen = client.cookies[COOKIE_NAME]

    rotated = await client.post("/api/v1/auth/refresh")  # the legitimate owner moves on
    assert rotated.status_code == 200
    user_id = uuid.UUID(first.json()["user"]["id"])

    # Past the race-condition grace window, so this is replay rather than a double click.
    async with get_sessionmaker()() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_not(None))
            .values(revoked_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.commit()

    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, stolen, domain="test.local", path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401

    # The owner's *current* token and access token are dead too.
    assert (await client.get("/api/v1/auth/me", headers=bearer(rotated))).status_code == 401
    async with get_sessionmaker()() as session:
        live = await session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        assert list(live) == []


async def test_two_refreshes_racing_do_not_log_the_user_out(client) -> None:
    """The second request simply lost a race: refused, but the account is left standing."""
    first = await register(client)
    token = client.cookies[COOKIE_NAME]

    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token, domain="test.local", path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    assert (await client.get("/api/v1/auth/me", headers=bearer(first))).status_code == 200


async def test_an_expired_refresh_token_is_refused(client) -> None:
    first = await register(client)
    async with get_sessionmaker()() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == uuid.UUID(first.json()["user"]["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_logging_out_revokes_the_refresh_token(client) -> None:
    await register(client)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_a_token_that_is_not_stored_is_refused(client) -> None:
    """A validly signed refresh token that was never issued (or was deleted) gets nowhere."""
    first = await register(client)
    async with get_sessionmaker()() as session:
        for row in await session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == uuid.UUID(first.json()["user"]["id"]))
        ):
            await session.delete(row)
        await session.commit()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


# ── Changing a password ──────────────────────────────────────────────────────
async def test_changing_a_password_retires_every_existing_token(client) -> None:
    address = email()
    first = await register(client, address)

    changed = await client.post(
        "/api/v1/auth/change-password",
        headers=bearer(first),
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
    )
    assert changed.status_code == 204

    # The access token issued before the change stops working at once, not at expiry.
    assert (await client.get("/api/v1/auth/me", headers=bearer(first))).status_code == 401
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    old = await client.post("/api/v1/auth/login", json={"email": address, "password": PASSWORD})
    new = await client.post(
        "/api/v1/auth/login", json={"email": address, "password": "a-brand-new-password"}
    )
    assert old.status_code == 401 and new.status_code == 200


async def test_changing_a_password_needs_the_current_one(client) -> None:
    first = await register(client)
    response = await client.post(
        "/api/v1/auth/change-password",
        headers=bearer(first),
        json={"current_password": "wrong-wrong-wrong", "new_password": "a-brand-new-password"},
    )
    assert response.status_code == 400
