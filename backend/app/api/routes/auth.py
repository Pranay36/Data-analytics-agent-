"""Sign-up, sign-in, and session endpoints.

The access token is returned in the body and held in memory by the client. The refresh token
never appears in a body at all: it is set as an httpOnly cookie, so script running on the page
(an XSS, a compromised dependency) cannot read it. The cookie is scoped to this router's path,
so it is not sent with every API request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import ActiveUser, SessionDep, get_active_user
from app.core.config import get_settings
from app.schemas.auth import (
    ChangePasswordIn,
    LimitsOut,
    LoginIn,
    MeOut,
    RegisterIn,
    TokenOut,
    UsageOut,
    UserOut,
)
from app.services import auth_service as auth
from app.services import usage_service

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "insightflow_refresh"
COOKIE_PATH = "/api/v1/auth"


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.auth_refresh_token_days * 86400,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path=COOKIE_PATH,
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)


def _token_out(user, access: str, expires_in: int) -> TokenOut:
    return TokenOut(access_token=access, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn, request: Request, response: Response, session: SessionDep
) -> TokenOut:
    try:
        user = await auth.register(
            session, email=body.email, password=body.password, full_name=body.full_name
        )
    except auth.RegistrationClosed as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is closed.") from exc
    except auth.EmailTaken as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with that email already exists."
        ) from exc

    access, expires_in, refresh = await auth.issue_session(
        session, user, user_agent=request.headers.get("user-agent")
    )
    await session.commit()
    _set_cookie(response, refresh)
    return _token_out(user, access, expires_in)


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep
) -> TokenOut:
    try:
        user = await auth.authenticate(session, email=body.email, password=body.password)
    except auth.InvalidCredentials as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect."
        ) from exc
    except auth.AccountDisabled as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled.") from exc

    access, expires_in, refresh = await auth.issue_session(
        session, user, user_agent=request.headers.get("user-agent")
    )
    await session.commit()
    _set_cookie(response, refresh)
    return _token_out(user, access, expires_in)


@router.post("/refresh", response_model=TokenOut, responses={401: {"description": "Session ended"}})
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> TokenOut | JSONResponse:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    try:
        user, access, expires_in, new_token = await auth.rotate(
            session, token, user_agent=request.headers.get("user-agent")
        )
    except auth.InvalidRefreshToken:
        # A detected reuse has already ended the account's sessions; that must be saved even
        # though this request is refused. The cookie is cleared on a response of our own,
        # because an exception would discard anything set on `response`.
        await session.commit()
        failure = JSONResponse(
            {"detail": "Your session has ended. Sign in again."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        _clear_cookie(failure)
        return failure

    await session.commit()
    _set_cookie(response, new_token)
    return _token_out(user, access, expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    if token:
        await auth.revoke(session, token)
        await session.commit()
    _clear_cookie(response)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_active_user)],
)
async def change_password(body: ChangePasswordIn, user: ActiveUser, session: SessionDep) -> None:
    """Ends every other session, this one included: sign in again with the new password."""
    try:
        await auth.change_password(
            session,
            user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except auth.WrongPassword as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.") from exc
    await session.commit()


@router.get("/me", response_model=MeOut, dependencies=[Depends(get_active_user)])
async def me(user: ActiveUser, session: SessionDep) -> MeOut:
    used = await usage_service.today(session, user.id)
    cap = usage_service.limits()
    return MeOut(
        user=UserOut.model_validate(user),
        usage_today=UsageOut(
            analyses=used.analyses,
            llm_calls=used.llm_calls,
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
        ),
        limits=LimitsOut(
            analyses_per_day=cap.analyses_per_day,
            llm_calls_per_day=cap.llm_calls_per_day,
            tokens_per_day=cap.tokens_per_day,
        ),
        resets_at=usage_service.next_reset(),
    )
