"""Gap-free, per-year human-readable numbers (student codes, receipts, submissions).

The increment runs inside the caller's transaction: if that transaction rolls back,
the number is released as well.
"""

from sqlalchemy import BigInteger, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class Counter(Base):
    __tablename__ = "counters"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)


_NEXT_SQL = text(
    "INSERT INTO counters (key, value) VALUES (:key, 1) "
    "ON CONFLICT (key) DO UPDATE SET value = counters.value + 1 RETURNING value"
)


async def next_value(db: AsyncSession, key: str) -> int:
    return int((await db.execute(_NEXT_SQL, {"key": key})).scalar_one())


async def next_code(db: AsyncSession, kind: str, year: int, template: str) -> str:
    """e.g. next_code(db, "student", 2026, "MSRF-{year}-{n:03d}") -> "MSRF-2026-001"."""
    n = await next_value(db, f"{kind}:{year}")
    return template.format(year=year, n=n)
