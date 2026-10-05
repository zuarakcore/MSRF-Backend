"""Application factory.

Run locally:  uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import db_models  # noqa: F401  (imports every model so SQLAlchemy can map relationships)
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.redis import close_redis

OPENAPI_TAGS = [
    {"name": "Authentication", "description": "Login, token refresh, password reset and invites."},
    {"name": "Dashboard", "description": "Admin dashboard aggregates and global search."},
    {"name": "Students", "description": "Roster, admissions, documents, CSV import/export."},
    {"name": "Coaches", "description": "Coach accounts (invite-based), categories, documents."},
    {"name": "Categories", "description": "Student categories; also define coach scope."},
    {"name": "Program Types", "description": "Reference data."},
    {"name": "Training Centers", "description": "Reference data."},
    {"name": "Attendance & Sessions", "description": "Admin read-only views of sessions and attendance."},
    {"name": "Performance Reports", "description": "15-skill player development reports."},
    {"name": "Fees & Payments", "description": "Monthly fee ledger, payments, receipts, voids."},
    {"name": "Payment Verification", "description": "Review parent payment submissions."},
    {"name": "Website CMS", "description": "Programmes, team, gallery, jobs, applications, enquiries."},
    {"name": "Notifications", "description": "In-app notifications for the signed-in user."},
    {"name": "Coach Portal", "description": "Endpoints for coaches, scoped to their categories."},
    {"name": "Public Website", "description": "Anonymous endpoints used by the Next.js website."},
    {"name": "Files", "description": "Uploads and signed downloads."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.DEBUG)

    docs_url = "/api/docs" if settings.DOCS_ENABLED else None
    redoc_url = "/api/redoc" if settings.DOCS_ENABLED else None
    openapi_url = "/api/openapi.json" if settings.DOCS_ENABLED else None

    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="Backend for the MSRF admin/coach portal and public website.",
        openapi_tags=OPENAPI_TAGS,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
        debug=settings.DEBUG,
    )

    register_exception_handlers(app)

    # Starlette runs middleware in reverse order of addition: the last added is outermost.
    # Host and body-size checks sit *inside* CORS, so their 400/413 responses still carry CORS
    # headers and the browser shows the real error instead of a misleading "CORS error".
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BYTES)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
        max_age=600,
    )
    app.add_middleware(
        RequestContextMiddleware,
        hsts=settings.is_production,
        # Pages that render HTML (API docs, admin panel) get their own CSP instead of the strict API one.
        docs_paths=("/api/docs", "/api/redoc", "/api/openapi.json", "/admin"),
        embeddable_paths=("/media/", "/static/", f"{settings.API_PREFIX}/files/"),
        allow_private_network=not settings.is_production,
    )

    app.include_router(api_router, prefix=settings.API_PREFIX)

    if settings.ADMIN_PANEL_ENABLED:
        from app.admin_site import mount_admin

        mount_admin(app)

    # Built-in assets shipped with the code (the default placeholder image).
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    if settings.STORAGE_BACKEND == "local":
        # Development only: serve PUBLIC files (gallery, team photos). In production a CDN/bucket does this.
        public_dir = Path(settings.MEDIA_ROOT) / "public"
        public_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/media/public", StaticFiles(directory=public_dir), name="public-media")
    return app


app = create_app()
