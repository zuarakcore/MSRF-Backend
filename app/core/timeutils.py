"""Time helpers. Business dates ("today", due dates, 7-day locks) use the academy's timezone."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def today_local() -> date:
    return datetime.now(ZoneInfo(get_settings().TIMEZONE)).date()


def period_bounds(year: int, month: int | None = None) -> tuple[date, date]:
    """[start, end) of a month, or of the whole year when month is None."""
    if month:
        return date(year, month, 1), date(year + (month == 12), month % 12 + 1, 1)
    return date(year, 1, 1), date(year + 1, 1, 1)


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)
