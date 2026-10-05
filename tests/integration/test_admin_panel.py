"""The /admin back office: who can sign in, and every page renders.

The panel opens its own DB sessions, so these tests commit real rows (through a separate session,
not the rolled-back `db` fixture) and delete everything afterwards.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.admin_site import VIEWS
from app.core.database import get_sessionmaker
from app.core.security import hash_password
from app.db_models import Base
from app.main import app
from app.modules.users.models import Role, User
from tests.conftest import DEFAULT_PASSWORD
from tests.factories import make_coach, make_refs, make_student


@pytest.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[dict[str, Any]]:
    async with get_sessionmaker()() as db:
        db.add(
            User(
                email="boss@example.com",
                full_name="Boss",
                role=Role.ADMIN,
                password_hash=hash_password(DEFAULT_PASSWORD),
                is_verified=True,
            )
        )
        cat, pt, tc = await make_refs(db)
        student = await make_student(db, cat, pt, tc, full_name="Panel Test Student")
        coach = await make_coach(db, [cat])
        data = {"student_id": student.id, "coach_email": coach.user.email}
        await db.commit()
    yield data
    async with get_sessionmaker()() as db:
        for table in reversed(Base.metadata.sorted_tables):
            await db.execute(table.delete())
        await db.commit()


async def _panel(email: str, password: str) -> tuple[AsyncClient, int]:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post("/admin/login", data={"username": email, "password": password})
    return client, response.status_code


async def test_only_admins_can_sign_in(committed: dict[str, Any]) -> None:
    for email, password in (
        (committed["coach_email"], DEFAULT_PASSWORD),
        ("boss@example.com", "wrong-password"),
    ):
        client, status = await _panel(email, password)
        assert status == 400, email  # login form shown again with an error
        assert (await client.get("/admin/")).status_code == 302  # still redirected to login
        await client.aclose()

    client, status = await _panel("boss@example.com", DEFAULT_PASSWORD)
    assert status == 302
    assert (await client.get("/admin/")).status_code == 200
    await client.aclose()


async def test_every_admin_page_renders(committed: dict[str, Any]) -> None:
    client, _ = await _panel("boss@example.com", DEFAULT_PASSWORD)
    try:
        for view in VIEWS:
            response = await client.get(f"/admin/{view.identity}/list")
            assert response.status_code == 200, f"{view.identity}: {response.status_code}"
        assert "Panel Test Student" in (await client.get("/admin/student/list")).text
        sid = committed["student_id"]
        assert (await client.get(f"/admin/student/details/{sid}")).status_code == 200
        assert (await client.get(f"/admin/student/edit/{sid}")).status_code == 200
        assert (await client.get("/admin/payment/create")).status_code in (403, 404)  # read-only section
    finally:
        await client.aclose()


async def test_overview_and_readable_lists(committed: dict[str, Any]) -> None:
    client, _ = await _panel("boss@example.com", DEFAULT_PASSWORD)
    try:
        home = (await client.get("/admin/")).text
        assert "Active students" in home and "Payments to verify" in home and "Collected this month" in home

        students = (await client.get("/admin/student/list")).text
        assert "Student ID" in students and "Monthly fee" in students  # human column labels
        assert 'class="badge bg-green-lt">Active<' in students  # status badge, not "ACTIVE"
        assert "₹" in students  # money formatted as rupees

        coaches = (await client.get("/admin/coach-profile/list")).text
        assert committed["coach_email"] in coaches and "<User " not in coaches  # related rows by name

        assert "Panel Test Student" in (await client.get("/admin/student/list?status=ACTIVE")).text
        assert "Panel Test Student" not in (await client.get("/admin/student/list?status=INACTIVE")).text
    finally:
        await client.aclose()
