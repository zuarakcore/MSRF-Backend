"""Role checks, plus a guard that every non-public route in the real app requires auth."""

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CoachUser, require_admin
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.main import app as real_app
from app.modules.users.models import Role
from tests.conftest import UserFactory, bearer, login

# Routes that are intentionally reachable without a bearer token.
PUBLIC_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
    ("GET", "/api/v1/files/{file_id}/content"),  # authorised by its HMAC signature, not a bearer token
}
PUBLIC_PREFIXES = ("/api/v1/public/",)  # website endpoints


def _probe_app(db: AsyncSession) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/admin-only")
    async def admin_only(user: AdminUser) -> dict[str, str]:
        return {"role": user.role}

    @app.get("/coach-only")
    async def coach_only(user: CoachUser) -> dict[str, str]:
        return {"role": user.role}

    guarded = APIRouter(dependencies=[Depends(require_admin)])

    @guarded.get("/router-guarded")
    async def router_guarded() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(guarded)

    async def _db() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_db] = _db
    return app


@pytest.mark.parametrize(
    ("role", "path", "expected"),
    [
        (Role.ADMIN, "/admin-only", 200),
        (Role.COACH, "/admin-only", 403),
        (Role.COACH, "/coach-only", 200),
        (Role.ADMIN, "/coach-only", 403),
        (Role.ADMIN, "/router-guarded", 200),
        (Role.COACH, "/router-guarded", 403),
    ],
)
async def test_role_enforcement(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession, role: Role, path: str, expected: int
) -> None:
    await make_user(email="u@example.com", role=role)
    token = await login(client, "u@example.com")
    async with AsyncClient(transport=ASGITransport(app=_probe_app(db)), base_url="https://test") as probe:
        response = await probe.get(path, headers=bearer(token))
    assert response.status_code == expected
    if expected == 403:
        assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("path", ["/admin-only", "/coach-only", "/router-guarded"])
async def test_anonymous_rejected(db: AsyncSession, path: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=_probe_app(db)), base_url="https://test") as probe:
        response = await probe.get(path)
    assert response.status_code == 401


def _protected_routes() -> list[tuple[str, str]]:
    # The OpenAPI schema lists every documented route with its full path, however routers are nested.
    paths = real_app.openapi()["paths"]
    routes = [
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if (method.upper(), path) not in PUBLIC_ROUTES and not path.startswith(PUBLIC_PREFIXES)
    ]
    assert routes, "route discovery found nothing; the guard below would silently pass"
    return sorted(routes)


@pytest.mark.parametrize(("method", "path"), _protected_routes())
async def test_every_non_public_route_requires_authentication(
    client: AsyncClient, method: str, path: str
) -> None:
    """New routes are covered automatically. A failure here means a route is missing its auth dependency,
    or it is intentionally public and must be added to PUBLIC_ROUTES."""
    url = path.replace("{", "").replace("}", "")  # placeholders become dummy segments
    response = await client.request(method, url)
    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"
