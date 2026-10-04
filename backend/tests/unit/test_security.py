"""Password hashing and token handling: pure functions, no database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    ALGORITHM,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)

KEY = "unit-test-signing-key-" + "k" * 40


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── Passwords ────────────────────────────────────────────────────────────────
def test_a_password_is_never_stored_as_itself() -> None:
    hashed = hash_password("hunter2hunter2")
    assert "hunter2" not in hashed
    assert hashed.startswith("$argon2id$")


def test_the_right_password_verifies_and_a_wrong_one_does_not() -> None:
    hashed = hash_password("correct horse")
    assert verify_password("correct horse", hashed)
    assert not verify_password("correct horsf", hashed)


def test_two_hashes_of_one_password_differ() -> None:
    """Each is salted, so equal passwords are not visible as equal in the table."""
    assert hash_password("same") != hash_password("same")


def test_an_unknown_account_still_costs_a_verification() -> None:
    """`None` stands for "no such email". It must fail, not skip the work (a timing leak)."""
    assert verify_password("anything", None) is False


def test_a_placeholder_hash_can_never_verify() -> None:
    """The system account has no usable password; verifying against it must not raise."""
    assert verify_password("anything", "!") is False


# ── Access tokens ────────────────────────────────────────────────────────────
def test_an_access_token_round_trips() -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id, token_version=3)
    claims = decode_token(token, expected_type="access")
    assert claims["sub"] == str(user_id)
    assert claims["tv"] == 3
    assert expires_in == get_settings().auth_access_token_minutes * 60


def test_an_expired_token_is_rejected() -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "access", "tv": 0, "iat": past - timedelta(hours=1),
         "exp": past, "jti": "x"},
        KEY, algorithm=ALGORITHM,
    )
    with pytest.raises(TokenError):
        decode_token(token, expected_type="access")


def test_a_token_signed_with_another_key_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "access", "tv": 0,
         "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1), "jti": "x"},
        "some-other-key-" + "z" * 40, algorithm=ALGORITHM,
    )
    with pytest.raises(TokenError):
        decode_token(forged, expected_type="access")


def test_a_token_with_alg_none_is_rejected() -> None:
    """The classic bypass: an unsigned token that claims it needs no signature."""
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "access", "tv": 0,
         "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1), "jti": "x"},
        key=None, algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_token(unsigned, expected_type="access")


def test_garbage_is_rejected() -> None:
    with pytest.raises(TokenError):
        decode_token("not.a.token", expected_type="access")


def test_a_refresh_token_cannot_be_used_as_an_access_token() -> None:
    token, _, _ = create_refresh_token(uuid.uuid4())
    with pytest.raises(TokenError, match="access"):
        decode_token(token, expected_type="access")


def test_an_access_token_cannot_be_used_as_a_refresh_token() -> None:
    token, _ = create_access_token(uuid.uuid4(), 0)
    with pytest.raises(TokenError, match="refresh"):
        decode_token(token, expected_type="refresh")


# ── Refresh tokens ───────────────────────────────────────────────────────────
def test_refresh_token_ids_match_their_claim() -> None:
    token, jti, expires = create_refresh_token(uuid.uuid4())
    assert decode_token(token, expected_type="refresh")["jti"] == str(jti)
    assert expires > datetime.now(UTC) + timedelta(days=get_settings().auth_refresh_token_days - 1)


def test_only_a_digest_of_the_refresh_token_is_stored() -> None:
    token, _, _ = create_refresh_token(uuid.uuid4())
    digest = hash_token(token)
    assert digest != token and len(digest) == 64
    assert hash_token(token) == digest


# ── The signing key itself ───────────────────────────────────────────────────
@pytest.mark.parametrize("weak", ["", "short", "change-me-" + "x" * 40, "secret" + "s" * 40])
def test_a_missing_or_weak_key_stops_the_app_from_signing(monkeypatch, weak) -> None:
    monkeypatch.setenv("AUTH_SECRET_KEY", weak)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        create_access_token(uuid.uuid4(), 0)
