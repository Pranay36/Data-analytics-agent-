"""The dependency chain that stands between a request and any protected route.

Each layer builds on the one before, and every route needs only the layer it cares about:

    get_current_user   a valid, unexpired access token for an account that still exists
    get_active_user    ...and that account is allowed to use the system
    enforce_quota      ...and has not spent today's allowance (only where model calls are spent)

They are attached to *routers*, not to individual handlers. A route added to a protected
router is therefore protected without anyone remembering to decorate it; the opposite design
fails open the first time someone forgets. `tests/graph/test_route_protection.py` walks every
registered route to hold that line.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_token
from app.db.models import User
from app.db.session import get_session
from app.services import usage_service

# `auto_error=False` so a missing header is our 401 with our message, not FastAPI's 403.
bearer = HTTPBearer(auto_error=False)


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        message,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None:
        raise _unauthenticated("Not signed in.")
    try:
        claims = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise _unauthenticated("Your session has expired. Sign in again.") from exc

    user = await session.get(User, uuid.UUID(claims["sub"]))
    # `tv` is the point of the token_version column: a password change retires every token
    # issued before it, without waiting for them to expire.
    if user is None or user.token_version != claims.get("tv"):
        raise _unauthenticated("Your session is no longer valid. Sign in again.")
    return user


async def get_active_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled.")
    return user


async def enforce_quota(
    user: Annotated[User, Depends(get_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    exceeded = await usage_service.over_quota(session, user)
    if exceeded is not None:
        reset = usage_service.next_reset().strftime("%H:%M UTC")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You have reached today's {exceeded} limit. It resets at {reset}.",
        )


async def require_admin(user: Annotated[User, Depends(get_active_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrators only.")
    return user


ActiveUser = Annotated[User, Depends(get_active_user)]
AdminUser = Annotated[User, Depends(require_admin)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
