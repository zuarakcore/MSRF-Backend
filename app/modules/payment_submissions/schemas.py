import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Self

from fastapi import UploadFile
from pydantic import BeforeValidator, Field, field_validator, model_validator

from app.core.schemas import CamelModel, InputModel, MoneyOut, PositiveMoney
from app.core.timeutils import today_local
from app.core.validators import Name, OptionalText500, OptStr80, OptStr120, OptStr2048, Phone
from app.modules.fees.schemas import AllocationIn, DiscountIn, check_unique_months
from app.modules.files.schemas import FileRef
from app.modules.payment_submissions.models import SubmissionMethod, SubmissionStatus
from app.modules.reference.schemas import RefItem

MAX_AGE_DAYS = 180


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


class SubmissionForm(InputModel):
    """The public multipart form. FastAPI reads fields (camelCase) and the file part into this model."""

    student_name: Name
    parent_mobile: Phone
    # The website sends 'UPI' / 'Cash': accept any casing.
    payment_method: Annotated[SubmissionMethod, BeforeValidator(_upper)]
    amount: PositiveMoney
    payment_date: date
    transaction_reference: OptStr80 = None
    transaction_id: OptStr80 = None  # legacy name used by the website's older payment dialog
    handed_over_to: OptStr120 = None
    captcha_token: OptStr2048 = None
    screenshot: UploadFile | None = None

    @field_validator("payment_date")
    @classmethod
    def _recent(cls, value: date) -> date:
        today = today_local()
        if value > today:
            raise ValueError("Payment date cannot be in the future")
        if value < today - timedelta(days=MAX_AGE_DAYS):
            raise ValueError(f"Payment date must be within the last {MAX_AGE_DAYS} days")
        return value

    @model_validator(mode="after")
    def _cash_receiver(self) -> Self:
        if self.transaction_reference is None and self.transaction_id:
            self.transaction_reference = self.transaction_id
        if self.payment_method is SubmissionMethod.CASH and not self.handed_over_to:
            raise ValueError("Enter the name of the person you handed the cash to")
        return self


class SubmissionCreated(CamelModel):
    submission_number: str
    status: SubmissionStatus


class CandidateStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    parent_name: str
    parent_phone: str
    category: RefItem


class SubmissionOut(CamelModel):
    id: uuid.UUID
    submission_number: str
    student_name: str
    parent_mobile: str
    amount: MoneyOut
    payment_date: date
    payment_method: SubmissionMethod
    transaction_reference: str | None
    handed_over_to: str | None
    screenshot: FileRef | None
    status: SubmissionStatus
    rejection_reason: str | None
    remarks: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    payment_id: uuid.UUID | None
    created_at: datetime
    candidate_students: list[CandidateStudent] | None = None


class SubmissionPatch(InputModel):
    student_name: Name | None = None
    remarks: OptionalText500 = None


class VerifyIn(InputModel):
    student_id: uuid.UUID
    allocations: Annotated[list[AllocationIn], Field(min_length=1, max_length=24)]
    discount: DiscountIn | None = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        check_unique_months(self.allocations)
        return self


class RejectIn(InputModel):
    reason: str = Field(min_length=5, max_length=300)
