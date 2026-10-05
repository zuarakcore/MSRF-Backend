"""Shared route dependencies.

Django/DRF equivalent: `request.user` + `permission_classes`. Here each is a dependency
injected into the route signature, e.g.:

    @router.get("/students")
    async def list_students(admin: AdminUser, db: DbSession): ...

Routers apply role checks at router level (`APIRouter(dependencies=[Depends(require_admin)])`)
so a new route cannot forget them.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import Forbidden, Unauthorized
from app.core.rate_limit import enforce
from app.core.security import decode_access_token
from app.modules.coaches.models import CoachProfile
from app.modules.users.models import Role, User

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False: we raise our own 401 with the standard error body.
_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized()
    claims = decode_access_token(credentials.credentials)

    # One primary-key lookup per request, so deactivation and password changes
    # take effect immediately instead of when the token expires.
    user = await db.get(User, claims.user_id)
    if user is None or user.token_version != claims.token_version:
        raise Unauthorized("Session is no longer valid", code="TOKEN_INVALID")
    if not user.is_active:
        raise Forbidden("This account is inactive", code="ACCOUNT_INACTIVE")
    # Generous per-user ceiling; fail open so a Redis outage does not take the admin panel down.
    await enforce("api-user", "300/minute", str(user.id), fail_open=True)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """The role always comes from the database row, never from client input."""

    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise Forbidden()
        return user

    return checker


require_admin = require_role(Role.ADMIN)
require_coach = require_role(Role.COACH)

AdminUser = Annotated[User, Depends(require_admin)]
CoachUser = Annotated[User, Depends(require_coach)]


async def get_current_coach(user: CoachUser, db: DbSession) -> CoachProfile:
    """The coach profile of the signed-in coach. Coach-portal services take it as their scope."""
    coach = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user.id))
    if coach is None:
        raise Forbidden("No coach profile is linked to this account", code="COACH_PROFILE_MISSING")
    return coach


CurrentCoach = Annotated[CoachProfile, Depends(get_current_coach)]
