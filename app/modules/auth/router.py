"""Authentication endpoints.

The access token is returned in the JSON body (kept in memory by the SPA).
The refresh token is set as an httpOnly cookie scoped to /api/v1/auth, so it is only
ever sent to these endpoints. Those endpoints also check the Origin header (CSRF).
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.errors import Forbidden
from app.core.rate_limit import RateLimit, client_ip, enforce
from app.core.schemas import MessageOut
from app.modules.auth import service
from app.modules.auth.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
)
from app.modules.coaches.models import CoachProfile
from app.modules.users.models import Role, User

router = APIRouter(prefix="/auth", tags=["Authentication"])

settings = get_settings()
REFRESH_COOKIE_PATH = f"{settings.API_PREFIX}/auth"


def verify_origin(request: Request) -> None:
    """CSRF defence for cookie-authenticated endpoints (in addition to SameSite=Strict).

    Browsers always send Origin on cross-origin POSTs. Requests without Origin come from
    non-browser clients, which cannot be driven by a malicious site, so they are allowed.
    """
    origin = request.headers.get("origin")
    if origin is not None and not get_settings().origin_allowed(origin):
        raise Forbidden("Origin not allowed", code="ORIGIN_NOT_ALLOWED")


RefreshCookie = Annotated[str | None, Cookie(alias=settings.REFRESH_COOKIE_NAME, include_in_schema=False)]


def _client(request: Request) -> service.ClientInfo:
    return service.ClientInfo(ip_address=client_ip(request), user_agent=request.headers.get("user-agent"))


def _set_refresh_cookie(response: Response, tokens: service.IssuedTokens) -> None:
    response.set_cookie(
        key=get_settings().REFRESH_COOKIE_NAME,
        value=tokens.refresh_token,
        max_age=tokens.refresh_max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,  # browsers accept Secure cookies on http://localhost
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=get_settings().REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


async def user_out(db: AsyncSession, user: User) -> UserOut:
    coach_id = None
    if user.role is Role.COACH:
        coach_id = await db.scalar(select(CoachProfile.id).where(CoachProfile.user_id == user.id))
    return UserOut.model_validate(user).model_copy(update={"coach_id": coach_id})


async def _token_out(db: AsyncSession, tokens: service.IssuedTokens) -> TokenOut:
    return TokenOut(
        access_token=tokens.access_token, expires_in=tokens.expires_in, user=await user_out(db, tokens.user)
    )


@router.post(
    "/login",
    response_model=TokenOut,
    dependencies=[Depends(verify_origin), Depends(RateLimit("login-ip", "100/hour"))],
    summary="Log in with email and password",
)
async def login(body: LoginIn, request: Request, response: Response, db: DbSession) -> TokenOut:
    email = body.email.lower()
    ip = client_ip(request)
    await enforce("login-ip-email", "5/minute", ip, email)
    await enforce("login-email", "20/hour", email)
    tokens = await service.login(db, email, body.password, _client(request))
    _set_refresh_cookie(response, tokens)
    return await _token_out(db, tokens)


@router.post(
    "/refresh",
    response_model=TokenOut,
    dependencies=[Depends(verify_origin), Depends(RateLimit("refresh", "30/minute"))],
    summary="Exchange the refresh cookie for a new access token",
)
async def refresh(
    request: Request, response: Response, db: DbSession, cookie: RefreshCookie = None
) -> TokenOut:
    tokens = await service.refresh(db, cookie, _client(request))
    _set_refresh_cookie(response, tokens)
    return await _token_out(db, tokens)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_origin)],
    summary="Revoke the current login and clear the refresh cookie",
)
async def logout(response: Response, db: DbSession, cookie: RefreshCookie = None) -> Response:
    await service.logout(db, cookie)
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=UserOut, summary="Current user")
async def me(user: CurrentUser, db: DbSession) -> UserOut:
    return await user_out(db, user)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageOut,
    dependencies=[Depends(RateLimit("forgot-ip", "10/hour"))],
    summary="Email a password reset link (always returns 202)",
)
async def forgot_password(body: ForgotPasswordIn, db: DbSession) -> MessageOut:
    await enforce("forgot-email", "3/hour", body.email.lower())
    await service.request_password_reset(db, body.email.lower())
    return MessageOut(detail="If the account exists, an email has been sent.")


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RateLimit("reset-ip", "10/hour"))],
    summary="Set a new password using a reset or invite link token",
)
async def reset_password(body: ResetPasswordIn, db: DbSession) -> Response:
    await service.reset_password(db, body.token, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/change-password",
    response_model=TokenOut,
    summary="Change own password; other sessions are signed out",
)
async def change_password(
    body: ChangePasswordIn, request: Request, response: Response, user: CurrentUser, db: DbSession
) -> TokenOut:
    await enforce("change-password", "10/hour", str(user.id))
    tokens = await service.change_password(
        db, user, body.current_password, body.new_password, _client(request)
    )
    _set_refresh_cookie(response, tokens)
    return await _token_out(db, tokens)
