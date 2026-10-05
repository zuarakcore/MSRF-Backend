import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import pg_enum

if TYPE_CHECKING:
    from app.modules.students.models import Student
    from app.modules.users.models import User

Money = Numeric(12, 2)


class FeeLedgerEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """What one student owes for one month. Status (PAID/PENDING/OVERDUE) is computed, not stored."""

    __tablename__ = "fee_ledger_entries"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    period: Mapped[date] = mapped_column(Date)  # first day of the month
    amount_due: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0), server_default="0")
    discount_reason: Mapped[str | None] = mapped_column(String(300))
    # Sum of VALID payment allocations; maintained in the same transaction under a row lock.
    amount_paid: Mapped[Decimal] = mapped_column(Money, default=Decimal(0), server_default="0")
    due_date: Mapped[date] = mapped_column(Date)

    student: Mapped["Student"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("student_id", "period", name="uq_fee_ledger_entries_student_period"),
        Index("ix_fee_ledger_entries_period_student", "period", "student_id"),
        CheckConstraint("amount_due >= 0", name="amount_due_non_negative"),
        CheckConstraint("discount >= 0", name="discount_non_negative"),
        CheckConstraint("amount_paid >= 0", name="amount_paid_non_negative"),
        CheckConstraint("discount + amount_paid <= amount_due", name="not_overpaid"),
    )


class PaymentMode(StrEnum):
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    UPI = "UPI"
    CHEQUE = "CHEQUE"


class PaymentSource(StrEnum):
    MANUAL = "MANUAL"
    SUBMISSION = "SUBMISSION"


class PaymentStatus(StrEnum):
    VALID = "VALID"
    VOIDED = "VOIDED"


class Payment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Money received. Never edited or deleted; corrections are voids."""

    __tablename__ = "payments"

    receipt_number: Mapped[str] = mapped_column(String(30), unique=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    mode: Mapped[PaymentMode] = mapped_column(pg_enum(PaymentMode, "payment_mode"))
    paid_on: Mapped[date] = mapped_column(Date, index=True)
    reference: Mapped[str | None] = mapped_column(String(120))
    remarks: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[PaymentSource] = mapped_column(pg_enum(PaymentSource, "payment_source"))
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_submissions.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"), default=PaymentStatus.VALID, server_default="VALID"
    )
    void_reason: Mapped[str | None] = mapped_column(String(300))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student: Mapped["Student"] = relationship(lazy="selectin")
    allocations: Mapped[list["PaymentAllocation"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    recorded_by: Mapped["User | None"] = relationship(foreign_keys=[recorded_by_id], lazy="selectin")

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)


class PaymentAllocation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    ledger_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_ledger_entries.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Money)

    ledger_entry: Mapped[FeeLedgerEntry] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("payment_id", "ledger_entry_id", name="uq_payment_allocations_payment_entry"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )
