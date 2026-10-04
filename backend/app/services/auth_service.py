"""Accounts, sessions, and refresh-token rotation.

The rule that matters most here is in `rotate`: a refresh token is single-use. Using one
revokes it and issues its replacement, so a token that has *already* been exchanged should
never be seen again. If one is, a copy of it exists somewhere it should not, and there is
no way to tell the thief from the owner — so every session the account has is ended.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_and_update,
    verify_password,
)
from app.db.models import RefreshToken, User, normalise_email

logger = logging.getLogger(__name__)

# Two requests that both carry the same refresh cookie (a double-fired effect, two tabs
# waking together) are not theft: the second simply lost a race. Within this window a
# reused token is refused but does not end the account's sessions.
REUSE_GRACE = timedelta(seconds=10)

SYSTEM_EMAIL = "system@insightflow.local"
UNUSABLE_HASH = "!"  # not a valid Argon2 string, so no password can ever verify against it


class EmailTaken(ValueError):
    pass


class InvalidCredentials(Exception):
    """Wrong email or wrong password. Deliberately one error for both."""


class AccountDisabled(Exception):
    pass


class InvalidRefreshToken(Exception):
    pass


class WrongPassword(Exception):
    pass


class RegistrationClosed(Exception):
    pass


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalise_email(email)))


async def register(
    session: AsyncSession, *, email: str, password: str, full_name: str | None = None
) -> User:
    if not get_settings().auth_allow_registration:
        raise RegistrationClosed
    email = normalise_email(email)
    if await get_by_email(session, email) is not None:
        raise EmailTaken(email)
    user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Check a login. The same work and the same error whether or not the address exists."""
    user = await get_by_email(session, email)
    # With no account this still performs one full verification, so a miss is not faster.
    valid, upgraded = verify_and_update(password, user.hashed_password if user else None)
    if user is None or not valid:
        raise InvalidCredentials
    if not user.is_active:
        raise AccountDisabled

    if upgraded:
        user.hashed_password = upgraded  # the hashing parameters were strengthened
    user.last_login_at = datetime.now(UTC)
    return user


async def issue_session(
    session: AsyncSession, user: User, *, user_agent: str | None = None
) -> tuple[str, int, str]:
    """Return (access token, its lifetime in seconds, refresh token)."""
    access, expires_in = create_access_token(user.id, user.token_version)
    refresh = await _store_refresh(session, user.id, user_agent)
    return access, expires_in, refresh[0]


async def _store_refresh(
    session: AsyncSession, user_id: uuid.UUID, user_agent: str | None
) -> tuple[str, RefreshToken]:
    token, jti, expires = create_refresh_token(user_id)
    row = RefreshToken(
        id=jti,
        user_id=user_id,
        token_hash=hash_token(token),
        expires_at=expires,
        user_agent=(user_agent or "")[:200] or None,
    )
    session.add(row)
    await session.flush()
    return token, row


async def rotate(
    session: AsyncSession, token: str, *, user_agent: str | None = None
) -> tuple[User, str, int, str]:
    """Exchange a refresh token for a new session. Returns (user, access, ttl, refresh).

    Raises InvalidRefreshToken for anything wrong, including detected reuse. In the reuse
    case the account's sessions are ended *before* raising, and the caller must commit.
    """
    try:
        decode_token(token, expected_type="refresh")
    except TokenError as exc:
        raise InvalidRefreshToken from exc

    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
    )
    if row is None:
        raise InvalidRefreshToken

    now = datetime.now(UTC)
    if row.revoked_at is not None:
        if now - row.revoked_at > REUSE_GRACE:
            logger.warning("refresh token reuse detected", extra={"user_id": str(row.user_id)})
            await end_all_sessions(session, row.user_id)
        raise InvalidRefreshToken

    if row.expires_at <= now:
        raise InvalidRefreshToken

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshToken

    new_token, new_row = await _store_refresh(session, user.id, user_agent)
    row.revoked_at = now
    row.replaced_by = new_row.id
    access, expires_in = create_access_token(user.id, user.token_version)
    return user, access, expires_in, new_token


async def revoke(session: AsyncSession, token: str) -> None:
    """Sign out one session. An unknown or already-revoked token is not an error."""
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


async def end_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every refresh token and retire every access token already issued."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        update(User).where(User.id == user_id).values(token_version=User.token_version + 1)
    )


async def change_password(
    session: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise WrongPassword
    user.hashed_password = hash_password(new_password)
    await end_all_sessions(session, user.id)


# ── Accounts the system creates for itself ───────────────────────────────────
async def ensure_system_user(session: AsyncSession) -> User:
    """The owner of work no person started: evaluation runs and the command-line tools.

    It cannot log in (its password hash matches nothing), so it is an accounting bucket and
    not a way in. Their spend is then visible as its own line rather than hiding in a real
    person's quota.
    """
    user = await get_by_email(session, SYSTEM_EMAIL)
    if user is None:
        user = User(
            email=SYSTEM_EMAIL,
            hashed_password=UNUSABLE_HASH,
            full_name="System",
            is_admin=True,
        )
        session.add(user)
        await session.flush()
    return user


async def ensure_seed_admin(session: AsyncSession) -> User | None:
    """Create the administrator named in settings, once. Returns None if none is configured."""
    settings = get_settings()
    password = settings.seed_admin_password.get_secret_value()
    if not settings.seed_admin_email or not password:
        return None
    existing = await get_by_email(session, settings.seed_admin_email)
    if existing is not None:
        return existing
    user = User(
        email=normalise_email(settings.seed_admin_email),
        hashed_password=hash_password(password),
        full_name="Administrator",
        is_admin=True,
    )
    session.add(user)
    await session.flush()
    return user
