"""Fee ledger, payments and receipts.

Integrity rules:
* Payments are never edited or deleted; a mistake is voided (allocations reversed) and re-entered.
* Ledger rows are locked (SELECT ... FOR UPDATE) while a payment or void changes them, so two
  admins recording at once cannot overpay a month; a DB check constraint is the final guard.
"""

import calendar
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.counters import next_code
from app.core.errors import BusinessRuleViolation, Conflict, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import period_bounds, today_local, utcnow
from app.modules.audit import service as audit
from app.modules.fees.models import (
    FeeLedgerEntry,
    Payment,
    PaymentAllocation,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
)
from app.modules.fees.schemas import (
    AllocationIn,
    AllocationOut,
    DiscountIn,
    LedgerMonth,
    LedgerPayment,
    LedgerRow,
    LedgerStudent,
    LedgerSummary,
    PaymentIn,
    PaymentOut,
    PaymentStudent,
    StudentFees,
)
from app.modules.reference.schemas import RefItem
from app.modules.students.models import Student
from app.modules.users.models import User

ZERO = Decimal("0.00")


def month_label(period: date) -> str:
    return f"{calendar.month_name[period.month]} {period.year}"


def _student_ref(s: Student) -> dict[str, Any]:
    return {
        "id": s.id,
        "student_code": s.student_code,
        "full_name": s.full_name,
        "parent_name": s.parent_name,
        "parent_phone": s.parent_phone,
        "category": RefItem.model_validate(s.category),
    }


def payment_out(p: Payment) -> PaymentOut:
    return PaymentOut(
        id=p.id,
        receipt_number=p.receipt_number,
        student=PaymentStudent.model_validate(_student_ref(p.student)),
        amount=p.amount,
        mode=p.mode,
        paid_on=p.paid_on,
        reference=p.reference,
        remarks=p.remarks,
        source=p.source,
        submission_id=p.submission_id,
        allocations=sorted(
            (
                AllocationOut(
                    year=a.ledger_entry.period.year,
                    month=a.ledger_entry.period.month,
                    label=month_label(a.ledger_entry.period),
                    amount=a.amount,
                )
                for a in p.allocations
            ),
            key=lambda a: (a.year, a.month),
        ),
        status=p.status,
        void_reason=p.void_reason,
        voided_at=p.voided_at,
        recorded_by=p.recorded_by.full_name if p.recorded_by else None,
        created_at=p.created_at,
    )


# --- ledger views ------------------------------------------------------------------------


def _ledger_query(
    *,
    year: int,
    month: int | None,
    category_id: uuid.UUID | None,
    status: str | None,
    search: str | None,
) -> Select[*tuple[Any, ...]]:
    """One row per student: sums over the month (or the whole year) with a derived status."""
    e = FeeLedgerEntry
    start, end = period_bounds(year, month)
    today = today_local()
    due, disc, paid = func.sum(e.amount_due), func.sum(e.discount), func.sum(e.amount_paid)
    overdue_months = func.count().filter(e.amount_paid + e.discount < e.amount_due, e.due_date < today)
    stmt: Select[*tuple[Any, ...]] = (
        select(
            Student,
            due.label("due"),
            disc.label("disc"),
            paid.label("paid"),
            overdue_months.label("overdue_months"),
            func.count().label("months"),
        )
        .join(e, e.student_id == Student.id)
        .where(e.period >= start, e.period < end)
        .group_by(Student.id)
        .order_by(Student.full_name)
    )
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                Student.full_name.ilike(like),
                Student.student_code.ilike(like),
                Student.parent_name.ilike(like),
                Student.parent_phone.like(like),
            )
        )
    if status == "PAID":
        stmt = stmt.having(paid + disc >= due)
    elif status == "OVERDUE":
        stmt = stmt.having(overdue_months > 0)
    elif status == "PENDING":
        stmt = stmt.having(paid + disc < due, overdue_months == 0)
    return stmt


def _row_status(due: Decimal, disc: Decimal, paid: Decimal, overdue_months: int) -> str:
    if paid + disc >= due:
        return "PAID"
    return "OVERDUE" if overdue_months else "PENDING"


async def _last_payments(db: AsyncSession, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, date]:
    if not student_ids:
        return {}
    rows = await db.execute(
        select(Payment.student_id, func.max(Payment.paid_on))
        .where(Payment.student_id.in_(student_ids), Payment.status == PaymentStatus.VALID)
        .group_by(Payment.student_id)
    )
    return dict(rows.all())


def _check_period(year: int, month: int | None) -> None:
    today = today_local()
    if year > today.year or (year == today.year and month and month > today.month):
        raise BusinessRuleViolation("The selected period is in the future", code="PERIOD_IN_FUTURE")


async def ledger_rows(db: AsyncSession, params: PageParams, **filters: Any) -> tuple[list[LedgerRow], int]:
    _check_period(filters["year"], filters.get("month"))
    stmt = _ledger_query(**filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(stmt.limit(params.page_size).offset(params.offset))).all()
    last = await _last_payments(db, [r[0].id for r in rows])
    items = [
        LedgerRow(
            student=LedgerStudent.model_validate(_student_ref(s)),
            amount_due=due,
            discount=disc,
            amount_paid=paid,
            outstanding=max(due - disc - paid, ZERO),
            status=_row_status(due, disc, paid, overdue),
            months=months,
            last_payment_on=last.get(s.id),
        )
        for s, due, disc, paid, overdue, months in rows
    ]
    return items, total


async def ledger_summary(db: AsyncSession, **filters: Any) -> LedgerSummary:
    _check_period(filters["year"], filters.get("month"))
    sub = _ledger_query(**filters).subquery()
    due, disc, paid, overdue = sub.c.due, sub.c.disc, sub.c.paid, sub.c.overdue_months
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(due), 0),
                func.coalesce(func.sum(paid), 0),
                func.coalesce(func.sum(disc), 0),
                func.count(),
                func.count().filter(paid + disc < due),
                func.count().filter(overdue > 0),
            )
        )
    ).one()
    expected, collected, discount, students, pending, overdue_count = row
    return LedgerSummary(
        expected=expected,
        collected=collected,
        discount=discount,
        outstanding=max(Decimal(expected) - Decimal(discount) - Decimal(collected), ZERO),
        student_count=students,
        pending_count=pending,
        overdue_count=overdue_count,
    )


async def student_fees(db: AsyncSession, student: Student, year: int) -> StudentFees:
    start, end = period_bounds(year)
    entries = list(
        await db.scalars(
            select(FeeLedgerEntry)
            .where(
                FeeLedgerEntry.student_id == student.id,
                FeeLedgerEntry.period >= start,
                FeeLedgerEntry.period < end,
            )
            .order_by(FeeLedgerEntry.period)
        )
    )
    allocations = (
        await db.execute(
            select(PaymentAllocation, Payment)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
            .where(
                PaymentAllocation.ledger_entry_id.in_([e.id for e in entries]),
                Payment.status == PaymentStatus.VALID,
            )
        )
    ).all()
    by_entry: dict[uuid.UUID, list[LedgerPayment]] = {}
    for alloc, pay in allocations:
        by_entry.setdefault(alloc.ledger_entry_id, []).append(
            LedgerPayment(
                payment_id=pay.id, receipt_number=pay.receipt_number, amount=alloc.amount, paid_on=pay.paid_on
            )
        )
    today = today_local()
    months = [
        LedgerMonth(
            ledger_entry_id=e.id,
            year=e.period.year,
            month=e.period.month,
            label=month_label(e.period),
            amount_due=e.amount_due,
            discount=e.discount,
            discount_reason=e.discount_reason,
            amount_paid=e.amount_paid,
            outstanding=max(e.amount_due - e.discount - e.amount_paid, ZERO),
            due_date=e.due_date,
            status="PAID"
            if e.amount_paid + e.discount >= e.amount_due
            else ("OVERDUE" if e.due_date < today else "PENDING"),
            payments=by_entry.get(e.id, []),
        )
        for e in entries
    ]
    return StudentFees(year=year, monthly_fee=student.monthly_fee, months=months)


# --- recording payments --------------------------------------------------------------------


async def _locked_entries(
    db: AsyncSession, student_id: uuid.UUID, months: Sequence[tuple[int, int]]
) -> dict[tuple[int, int], FeeLedgerEntry]:
    periods = [date(y, m, 1) for y, m in months]
    entries = list(
        await db.scalars(
            select(FeeLedgerEntry)
            .where(FeeLedgerEntry.student_id == student_id, FeeLedgerEntry.period.in_(periods))
            .order_by(FeeLedgerEntry.period)
            .with_for_update()
        )
    )
    found = {(e.period.year, e.period.month): e for e in entries}
    missing = [f"{calendar.month_abbr[m]} {y}" for y, m in months if (y, m) not in found]
    if missing:
        raise NotFound(
            f"No fee is due for {', '.join(missing)} "
            "(the student was not enrolled, or the month has not started)",
            code="LEDGER_MONTH_NOT_FOUND",
        )
    return found


async def record_payment(
    db: AsyncSession,
    *,
    student_id: uuid.UUID,
    paid_on: date,
    mode: PaymentMode,
    allocations: list[AllocationIn],
    discount: DiscountIn | None,
    reference: str | None,
    remarks: str | None,
    actor: User,
    source: PaymentSource = PaymentSource.MANUAL,
    submission_id: uuid.UUID | None = None,
) -> Payment:
    """Create a payment with its allocations and update the ledger. Does not commit."""
    student = await db.get(Student, student_id)
    if student is None:
        raise NotFound("Student not found", code="STUDENT_NOT_FOUND")

    months = [(a.year, a.month) for a in allocations]
    if discount:
        months.append((discount.year, discount.month))
    entries = await _locked_entries(db, student_id, sorted(set(months)))

    if discount:
        entry = entries[(discount.year, discount.month)]
        if entry.discount + entry.amount_paid + discount.amount > entry.amount_due:
            raise BusinessRuleViolation(
                f"Discount exceeds the outstanding amount for {month_label(entry.period)}", code="OVERPAYMENT"
            )
        entry.discount += discount.amount
        entry.discount_reason = discount.reason
        audit.record(
            db,
            "DISCOUNT_APPLIED",
            actor_user_id=actor.id,
            entity_type="fee_ledger_entry",
            entity_id=entry.id,
            changes={"amount": str(discount.amount), "reason": discount.reason},
        )

    total = ZERO
    rows: list[PaymentAllocation] = []
    for alloc in allocations:
        entry = entries[(alloc.year, alloc.month)]
        outstanding = entry.amount_due - entry.discount - entry.amount_paid
        if alloc.amount > outstanding:
            raise BusinessRuleViolation(
                f"{month_label(entry.period)}: only ₹{outstanding} is outstanding", code="OVERPAYMENT"
            )
        entry.amount_paid += alloc.amount
        total += alloc.amount
        rows.append(PaymentAllocation(ledger_entry_id=entry.id, amount=alloc.amount))

    payment = Payment(
        receipt_number=await next_code(db, "receipt", paid_on.year, "MSRF-RCPT-{year}-{n:05d}"),
        student_id=student_id,
        amount=total,
        mode=mode,
        paid_on=paid_on,
        reference=reference,
        remarks=remarks,
        source=source,
        submission_id=submission_id,
        recorded_by_id=actor.id,
    )
    payment.allocations = rows
    db.add(payment)
    await db.flush()
    audit.record(
        db,
        "PAYMENT_RECORDED",
        actor_user_id=actor.id,
        entity_type="payment",
        entity_id=payment.id,
        changes={"amount": str(total), "receipt": payment.receipt_number, "mode": mode.value},
    )
    return payment


async def _reload(db: AsyncSession, payment_id: uuid.UUID) -> Payment:
    payment = await db.scalar(
        select(Payment).where(Payment.id == payment_id).execution_options(populate_existing=True)
    )
    assert payment is not None
    return payment


async def create_payment(db: AsyncSession, data: PaymentIn, actor: User) -> PaymentOut:
    payment = await record_payment(
        db,
        student_id=data.student_id,
        paid_on=data.paid_on,
        mode=data.mode,
        allocations=data.allocations,
        discount=data.discount,
        reference=data.reference,
        remarks=data.remarks,
        actor=actor,
    )
    await db.commit()
    return payment_out(await _reload(db, payment.id))


async def void_payment(db: AsyncSession, payment_id: uuid.UUID, reason: str, actor: User) -> PaymentOut:
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None:
        raise NotFound("Payment not found", code="PAYMENT_NOT_FOUND")
    if payment.status is PaymentStatus.VOIDED:
        raise Conflict("This payment is already voided", code="ALREADY_VOIDED")
    entry_ids = [a.ledger_entry_id for a in payment.allocations]
    entries = {
        e.id: e
        for e in await db.scalars(
            select(FeeLedgerEntry).where(FeeLedgerEntry.id.in_(entry_ids)).with_for_update()
        )
    }
    for alloc in payment.allocations:
        entries[alloc.ledger_entry_id].amount_paid -= alloc.amount
    payment.status = PaymentStatus.VOIDED
    payment.void_reason = reason
    payment.voided_at = utcnow()
    payment.voided_by_id = actor.id
    audit.record(
        db,
        "PAYMENT_VOIDED",
        actor_user_id=actor.id,
        entity_type="payment",
        entity_id=payment.id,
        changes={"reason": reason, "amount": str(payment.amount)},
    )
    await db.commit()
    return payment_out(await _reload(db, payment_id))


async def list_payments(
    db: AsyncSession,
    params: PageParams,
    *,
    student_id: uuid.UUID | None = None,
    mode: PaymentMode | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
) -> tuple[list[PaymentOut], int]:
    stmt = select(Payment).join(Student, Student.id == Payment.student_id)
    if student_id:
        stmt = stmt.where(Payment.student_id == student_id)
    if mode:
        stmt = stmt.where(Payment.mode == mode)
    if date_from:
        stmt = stmt.where(Payment.paid_on >= date_from)
    if date_to:
        stmt = stmt.where(Payment.paid_on <= date_to)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                Payment.receipt_number.ilike(like),
                Student.full_name.ilike(like),
                Student.student_code.ilike(like),
                Payment.reference.ilike(like),
            )
        )
    stmt = stmt.order_by(Payment.paid_on.desc(), Payment.created_at.desc())
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    payments = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [payment_out(p) for p in payments], total


async def get_payment(db: AsyncSession, payment_id: uuid.UUID) -> PaymentOut:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise NotFound("Payment not found", code="PAYMENT_NOT_FOUND")
    return payment_out(payment)
