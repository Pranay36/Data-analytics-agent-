"""Password hashing and token handling.

Pure functions over strings and claims: no database, no request. That keeps the
cryptographic decisions in one small file that can be tested without fixtures, and it is
the only file to change if signing ever moves from HS256 to a key pair.

Two kinds of token, told apart by a `typ` claim so neither can be presented as the other:

- access: short-lived, stateless, sent on every request. It cannot be withdrawn, so it
  carries `tv` — the user's `token_version`. Changing a password bumps that integer and
  every token issued before it stops being accepted on the very next request.
- refresh: long-lived, stored *hashed* in the database and rotated on every use. Its row is
  what makes signing out real rather than cosmetic.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

_hasher = PasswordHash.recommended()  # Argon2id
# Verified against when the email is unknown, so a miss costs the same time as a hit and
# login timing does not reveal which addresses have accounts.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class TokenError(Exception):
    """The token is missing, malformed, expired, forged, or of the wrong type."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    """Constant-effort check; `hashed=None` still does the work of one verification."""
    if hashed is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    try:
        return _hasher.verify(password, hashed)
    except UnknownHashError:
        return False  # an account with a placeholder hash (the system user) cannot log in


def verify_and_update(password: str, hashed: str | None) -> tuple[bool, str | None]:
    """Verify, and return a replacement hash if the stored one uses outdated parameters.

    The caller stores the replacement: that is how hash strength is raised over time without
    asking anyone to reset their password.
    """
    if hashed is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False, None
    try:
        return _hasher.verify_and_update(password, hashed)
    except UnknownHashError:
        return False, None


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, get_settings().require_auth_secret(), algorithm=ALGORITHM)


def create_access_token(user_id: uuid.UUID, token_version: int) -> tuple[str, int]:
    """Return the token and its lifetime in seconds."""
    lifetime = timedelta(minutes=get_settings().auth_access_token_minutes)
    now = _now()
    token = _encode(
        {
            "sub": str(user_id),
            "typ": "access",
            "tv": token_version,
            "iat": now,
            "exp": now + lifetime,
            "jti": str(uuid.uuid4()),
        }
    )
    return token, int(lifetime.total_seconds())


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, uuid.UUID, datetime]:
    """Return the token, its id (also the `jti` claim), and when it expires."""
    jti = uuid.uuid4()
    now = _now()
    expires = now + timedelta(days=get_settings().auth_refresh_token_days)
    token = _encode(
        {"sub": str(user_id), "typ": "refresh", "iat": now, "exp": expires, "jti": str(jti)}
    )
    return token, jti, expires


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            get_settings().require_auth_secret(),
            algorithms=[ALGORITHM],  # never trust the header's own `alg`
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if claims["typ"] != expected_type:
        raise TokenError(f"expected a {expected_type} token")
    try:
        uuid.UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise TokenError("malformed subject") from exc
    return claims


def hash_token(token: str) -> str:
    """What is stored for a refresh token, so a leaked database cannot be replayed."""
    return hashlib.sha256(token.encode()).hexdigest()
