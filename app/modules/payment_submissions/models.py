import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import pg_enum

if TYPE_CHECKING:
    from app.modules.files.models import StoredFile
    from app.modules.users.models import User


class SubmissionMethod(StrEnum):
    UPI = "UPI"
    CASH = "CASH"


class SubmissionStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class PaymentSubmission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A parent's claim that they paid, sent from the website. Becomes a Payment once verified."""

    __tablename__ = "payment_submissions"

    def __str__(self) -> str:
        return f"Submission {self.submission_number}"

    submission_number: Mapped[str] = mapped_column(String(20), unique=True)
    student_name: Mapped[str] = mapped_column(String(120))
    parent_mobile: Mapped[str] = mapped_column(String(10), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_date: Mapped[date] = mapped_column(Date)
    payment_method: Mapped[SubmissionMethod] = mapped_column(pg_enum(SubmissionMethod, "submission_method"))
    transaction_reference: Mapped[str | None] = mapped_column(String(80))
    handed_over_to: Mapped[str | None] = mapped_column(String(120))
    screenshot_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT")
    )
    status: Mapped[SubmissionStatus] = mapped_column(
        pg_enum(SubmissionStatus, "submission_status"),
        default=SubmissionStatus.PENDING,
        server_default="PENDING",
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    remarks: Mapped[str | None] = mapped_column(String(500))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitter_ip: Mapped[str | None] = mapped_column(INET)

    screenshot: Mapped["StoredFile | None"] = relationship(lazy="selectin")
    reviewed_by: Mapped["User | None"] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "payment_method <> 'UPI' OR screenshot_file_id IS NOT NULL", name="upi_needs_screenshot"
        ),
        CheckConstraint("payment_method <> 'CASH' OR handed_over_to IS NOT NULL", name="cash_needs_receiver"),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL", name="rejection_needs_reason"
        ),
        Index("ix_payment_submissions_status_created", "status", "created_at"),
    )
