import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AdminUser, DbSession, require_admin
from app.core.pagination import Page, PageParams, Pagination, build_page
from app.core.timeutils import today_local
from app.modules.fees import service
from app.modules.fees.models import PaymentMode
from app.modules.fees.schemas import (
    FeeStatus,
    LedgerRow,
    LedgerSummary,
    PaymentIn,
    PaymentOut,
    StudentFees,
    VoidIn,
)
from app.modules.students.service import get_student

router = APIRouter(tags=["Fees & Payments"], dependencies=[Depends(require_admin)])


class LedgerFilters:
    def __init__(
        self,
        year: Annotated[int, Query(ge=2020, le=2100)],
        month: Annotated[int | None, Query(ge=1, le=12, description="Omit for the whole year")] = None,
        category_id: Annotated[uuid.UUID | None, Query(alias="categoryId")] = None,
        status_filter: Annotated[FeeStatus | None, Query(alias="status")] = None,
        search: Annotated[str | None, Query(max_length=100)] = None,
    ) -> None:
        self.values: dict[str, Any] = {
            "year": year,
            "month": month,
            "category_id": category_id,
            "status": status_filter,
            "search": search,
        }


Filters = Annotated[LedgerFilters, Depends()]


@router.get("/fees/ledger", response_model=Page[LedgerRow], summary="Month-wise (or annual) fee ledger")
async def fee_ledger(db: DbSession, pagination: Pagination, filters: Filters) -> Page[LedgerRow]:
    items, total = await service.ledger_rows(db, pagination, **filters.values)
    return build_page(items, total, pagination)


@router.get(
    "/fees/summary", response_model=LedgerSummary, summary="Totals for the same filters as the ledger"
)
async def fee_summary(db: DbSession, filters: Filters) -> LedgerSummary:
    return await service.ledger_summary(db, **filters.values)


@router.post(
    "/fees/payments",
    response_model=PaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment (cash desk, bank, UPI, cheque) against one or more months",
)
async def record_payment(body: PaymentIn, db: DbSession, admin: AdminUser) -> PaymentOut:
    return await service.create_payment(db, body, admin)


@router.get("/payments", response_model=Page[PaymentOut], summary="Payments / receipts")
async def list_payments(
    db: DbSession,
    pagination: Pagination,
    student_id: Annotated[uuid.UUID | None, Query(alias="studentId")] = None,
    mode: PaymentMode | None = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[PaymentOut]:
    items, total = await service.list_payments(
        db, pagination, student_id=student_id, mode=mode, date_from=date_from, date_to=date_to, search=search
    )
    return build_page(items, total, pagination)


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="Receipt data for printing")
async def get_payment(payment_id: uuid.UUID, db: DbSession) -> PaymentOut:
    return await service.get_payment(db, payment_id)


@router.post(
    "/payments/{payment_id}/void",
    response_model=PaymentOut,
    summary="Void a payment (reverses its allocations; the record is kept)",
)
async def void_payment(payment_id: uuid.UUID, body: VoidIn, db: DbSession, admin: AdminUser) -> PaymentOut:
    return await service.void_payment(db, payment_id, body.reason, admin)


@router.get(
    "/students/{student_id}/fees", response_model=StudentFees, summary="A student's 12-month fee ledger"
)
async def student_fees(
    student_id: uuid.UUID,
    db: DbSession,
    year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
) -> StudentFees:
    student = await get_student(db, student_id)
    return await service.student_fees(db, student, year or today_local().year)


@router.get(
    "/students/{student_id}/payments", response_model=list[PaymentOut], summary="A student's payments"
)
async def student_payments(student_id: uuid.UUID, db: DbSession) -> list[PaymentOut]:
    await get_student(db, student_id)
    items, _ = await service.list_payments(db, PageParams(page=1, page_size=100), student_id=student_id)
    return items
