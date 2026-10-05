import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.core.schemas import CamelModel, InputModel, MoneyOut, PositiveMoney
from app.core.validators import OptionalShort, OptionalText500, PastOrToday
from app.modules.fees.models import PaymentMode, PaymentSource, PaymentStatus
from app.modules.reference.schemas import RefItem

FeeStatus = Literal["PAID", "PENDING", "OVERDUE"]


class MonthRef(InputModel):
    year: int = Field(ge=2020, le=2100)
    month: int = Field(ge=1, le=12)


class AllocationIn(MonthRef):
    amount: PositiveMoney


class DiscountIn(MonthRef):
    amount: PositiveMoney
    reason: str = Field(min_length=3, max_length=300)


def check_unique_months(allocations: list[AllocationIn]) -> None:
    months = [(a.year, a.month) for a in allocations]
    if len(months) != len(set(months)):
        raise ValueError("Each month may appear only once in allocations")


class PaymentIn(InputModel):
    student_id: uuid.UUID
    paid_on: PastOrToday
    mode: PaymentMode
    reference: OptionalShort = None
    remarks: OptionalText500 = None
    allocations: Annotated[list[AllocationIn], Field(min_length=1, max_length=24)]
    discount: DiscountIn | None = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        check_unique_months(self.allocations)
        return self


class VoidIn(InputModel):
    reason: str = Field(min_length=5, max_length=300)


class LedgerPayment(CamelModel):
    payment_id: uuid.UUID
    receipt_number: str
    amount: MoneyOut
    paid_on: date


class LedgerMonth(CamelModel):
    ledger_entry_id: uuid.UUID
    year: int
    month: int
    label: str
    amount_due: MoneyOut
    discount: MoneyOut
    discount_reason: str | None
    amount_paid: MoneyOut
    outstanding: MoneyOut
    due_date: date
    status: FeeStatus
    payments: list[LedgerPayment]


class StudentFees(CamelModel):
    year: int
    monthly_fee: MoneyOut
    months: list[LedgerMonth]


class LedgerStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    parent_name: str
    parent_phone: str
    category: RefItem


class LedgerRow(CamelModel):
    student: LedgerStudent
    amount_due: MoneyOut
    discount: MoneyOut
    amount_paid: MoneyOut
    outstanding: MoneyOut
    status: FeeStatus
    months: int
    last_payment_on: date | None


class LedgerSummary(CamelModel):
    expected: MoneyOut
    collected: MoneyOut
    discount: MoneyOut
    outstanding: MoneyOut
    student_count: int
    pending_count: int
    overdue_count: int


class AllocationOut(CamelModel):
    year: int
    month: int
    label: str
    amount: MoneyOut


class PaymentStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    parent_name: str
    parent_phone: str
    category: RefItem


class PaymentOut(CamelModel):
    id: uuid.UUID
    receipt_number: str
    student: PaymentStudent
    amount: MoneyOut
    mode: PaymentMode
    paid_on: date
    reference: str | None
    remarks: str | None
    source: PaymentSource
    submission_id: uuid.UUID | None
    allocations: list[AllocationOut]
    status: PaymentStatus
    void_reason: str | None
    voided_at: datetime | None
    recorded_by: str | None
    created_at: datetime
