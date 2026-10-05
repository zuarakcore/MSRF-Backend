"""Test data builders. Plain functions: explicit, no magic."""

import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Batch, Gender, Relationship
from app.core.timeutils import today_local
from app.modules.coaches.models import CoachProfile
from app.modules.fees import ledger
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.students.models import Student
from app.modules.users.models import Role, User
from tests.conftest import DEFAULT_PASSWORD, bearer, login

_counter = {"n": 0}


def _next() -> int:
    _counter["n"] += 1
    return _counter["n"]


async def make_category(db: AsyncSession, name: str | None = None) -> Category:
    c = Category(name=name or f"Category {_next()}")
    db.add(c)
    await db.flush()
    return c


async def make_refs(db: AsyncSession) -> tuple[Category, ProgramType, TrainingCenter]:
    n = _next()
    cat = await make_category(db)
    pt = ProgramType(name=f"Program {n}")
    tc = TrainingCenter(name=f"Center {n}", location="Kozhikode")
    db.add_all([pt, tc])
    await db.flush()
    return cat, pt, tc


async def make_student(
    db: AsyncSession,
    category: Category,
    program_type: ProgramType,
    center: TrainingCenter,
    *,
    monthly_fee: Decimal = Decimal("2000"),
    parent_phone: str = "9447000001",
    admission: date | None = None,
    full_name: str | None = None,
) -> Student:
    n = _next()
    s = Student(
        student_code=f"T-{n:05d}",
        admission_number=f"TADM-{n:05d}",
        admission_date=admission or today_local().replace(day=1),
        full_name=full_name or f"Student {n}",
        date_of_birth=date(2013, 5, 14),
        gender=Gender.MALE,
        category_id=category.id,
        program_type_id=program_type.id,
        training_center_id=center.id,
        batch=Batch.MORNING,
        monthly_fee=monthly_fee,
        parent_name=f"Parent {n}",
        parent_relationship=Relationship.FATHER,
        parent_phone=parent_phone,
    )
    db.add(s)
    await db.flush()
    await ledger.open_first_entry(db, s)
    await db.refresh(s)
    return s


async def make_coach(
    db: AsyncSession,
    categories: list[Category],
    *,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
) -> CoachProfile:
    from app.core.security import hash_password

    n = _next()
    user = User(
        email=email or f"coach{n}@example.com",
        full_name=f"Coach {n}",
        role=Role.COACH,
        password_hash=hash_password(password),
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    coach = CoachProfile(
        user_id=user.id, phone="9847000000", experience_years=5, joined_date=date(2022, 1, 1)
    )
    coach.categories = categories
    db.add(coach)
    await db.flush()
    await db.refresh(coach)
    return coach


async def coach_headers(client: AsyncClient, coach: CoachProfile) -> dict[str, str]:
    return bearer(await login(client, coach.user.email))


def png_bytes(size: tuple[int, int] = (64, 48), exif: bool = False) -> bytes:
    img = Image.new("RGB", size, (0, 160, 80))
    out = io.BytesIO()
    if exif:
        exif_data = Image.Exif()
        exif_data[0x010F] = "SecretCameraMaker"  # Make
        img.save(out, format="JPEG", exif=exif_data)
    else:
        img.save(out, format="PNG")
    return out.getvalue()


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def session_body(
    category_ids: list[Any], attendance: list[Any] | None = None, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sessionDate": today_local().isoformat(),
        "categoryIds": [str(c) for c in category_ids],
        "venue": "Main Ground",
        "startTime": "06:00",
        "endTime": "08:00",
        "dailyTopic": "Pressing triggers",
        "explanation": "Compact shape in midfield.",
        "splits": [{"heading": "Warm-up", "durationMinutes": 15}],
        "attendance": attendance or [],
    }
    body.update(overrides)
    return body


def days_ago(n: int) -> str:
    return (today_local() - timedelta(days=n)).isoformat()
