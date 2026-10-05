"""Fee-ledger primitives shared by the API (async) and Celery jobs (sync).

Billing rules (OPEN_QUESTIONS Q3, recommended answers):
* One entry per active student per calendar month, due on the 10th.
* The first entry is the month the student is created in (or the admission month if later);
  historic months before go-live are not back-billed.
* `amount_due` snapshots the student's monthly fee when the entry is created.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Insert, and_, case, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordStatus
from app.core.timeutils import add_months, month_start, today_local
from app.modules.fees.models import FeeLedgerEntry
from app.modules.students.models import Student

DUE_DAY = 10


def due_date_for(period: date) -> date:
    return period + timedelta(days=DUE_DAY - 1)


def status_expr(today: date | None = None) -> ColumnElement[str]:
    """PAID / OVERDUE / PENDING computed in SQL, so it is never stale and can be filtered on."""
    e = FeeLedgerEntry
    return case(
        (e.amount_paid + e.discount >= e.amount_due, literal("PAID")),
        (e.due_date < (today or today_local()), literal("OVERDUE")),
        else_=literal("PENDING"),
    )


def outstanding_expr() -> ColumnElement[Any]:
    e = FeeLedgerEntry
    return func.greatest(e.amount_due - e.discount - e.amount_paid, 0)


def generate_month_stmt(period: date) -> Insert:
    """Create entries for every active student admitted on or before the end of `period`.

    Idempotent: ON CONFLICT DO NOTHING on (student_id, period), so re-running is safe.
    """
    next_month = add_months(period, 1)
    source = select(
        func.gen_random_uuid(),
        Student.id,
        literal(period),
        Student.monthly_fee,
        literal(due_date_for(period)),
    ).where(and_(Student.status == RecordStatus.ACTIVE, Student.admission_date < next_month))
    return (
        pg_insert(FeeLedgerEntry)
        .from_select(["id", "student_id", "period", "amount_due", "due_date"], source)
        .on_conflict_do_nothing(index_elements=["student_id", "period"])
    )


async def open_first_entry(db: AsyncSession, student: Student) -> None:
    """Called when a student is created (or reactivated): bill from this month or the admission month."""
    period = max(month_start(today_local()), month_start(student.admission_date))
    if period > month_start(today_local()):
        return  # future admission: the monthly job creates it when that month starts
    await db.execute(
        pg_insert(FeeLedgerEntry)
        .values(
            id=uuid.uuid4(),
            student_id=student.id,
            period=period,
            amount_due=student.monthly_fee,
            due_date=due_date_for(period),
        )
        .on_conflict_do_nothing(index_elements=["student_id", "period"])
    )
