"""Test fixtures.

* Real PostgreSQL (`msrf_test`), schema created once per run.
* Each test runs inside a transaction that is rolled back afterwards; service-level
  `commit()` calls only release a SAVEPOINT, so tests stay isolated.
* Real Redis on DB 15, flushed before each test (rate limits start from zero).
* Celery runs tasks inline; emails land in `app.core.email.outbox`.
"""

import os
import tempfile

# Must be set before any `app` import: settings are read once and cached.
os.environ.update(
    APP_ENV="test",
    DEBUG="false",
    DATABASE_URL=os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://msrf:msrf@localhost:5432/msrf_test"
    ),
    REDIS_URL=os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
    CELERY_BROKER_URL="memory://",
    CELERY_TASK_ALWAYS_EAGER="true",
    EMAIL_BACKEND="memory",
    JWT_SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256",
    CORS_ORIGINS="http://localhost:5173",
    CORS_ORIGIN_REGEX="",  # never inherit a developer's allow-all .env setting
    TRUSTED_HOSTS="test",
    ADMIN_APP_URL="http://admin.test",
    MEDIA_ROOT=tempfile.mkdtemp(prefix="msrf-test-media-"),
    API_BASE_URL="https://test",
    PUBLIC_MEDIA_BASE_URL="https://test/media/public",
)

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core import email as email_module
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import hash_password
from app.db_models import Base
from app.main import app as fastapi_app
from app.modules.users.models import Role, User

DEFAULT_PASSWORD = "correct-horse-battery"
ALLOWED_ORIGIN = "http://localhost:5173"


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().DATABASE_URL)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, autoflush=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture(autouse=True)
async def _clean_redis_and_outbox() -> AsyncIterator[None]:
    redis = Redis.from_url(get_settings().REDIS_URL)
    await redis.flushdb()
    await redis.aclose()
    email_module.outbox.clear()
    yield


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_db
    # https base URL so the Secure refresh cookie is stored and sent by httpx.
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


UserFactory = Callable[..., Coroutine[Any, Any, User]]


@pytest.fixture
def make_user(db: AsyncSession) -> UserFactory:
    counter = {"n": 0}

    async def _make(
        *,
        role: Role = Role.ADMIN,
        email: str | None = None,
        password: str | None = DEFAULT_PASSWORD,
        is_active: bool = True,
        full_name: str = "Test User",
    ) -> User:
        counter["n"] += 1
        user = User(
            email=email or f"user{counter['n']}@example.com",
            full_name=full_name,
            role=role,
            password_hash=hash_password(password) if password else None,
            is_active=is_active,
            is_verified=password is not None,
        )
        db.add(user)
        await db.flush()
        return user

    return _make


async def login(client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["accessToken"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
