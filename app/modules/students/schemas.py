import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import EmailStr, model_validator

from app.core.enums import AttendanceStatus, Batch, BloodGroup, Gender, RecordStatus, Relationship
from app.core.schemas import CamelModel, InputModel, MoneyIn, MoneyOut
from app.core.timeutils import today_local
from app.core.validators import (
    EmptyStrToNone,
    Name,
    OptionalPhone,
    OptionalShort,
    OptionalText300,
    OptionalText500,
    OptStr30,
    OptStr40,
    PastOrToday,
    Phone,
)
from app.modules.files.schemas import FileRef
from app.modules.reference.schemas import RefItem

OptionalEmail = Annotated[EmailStr | None, EmptyStrToNone]


def _check_dob(dob: date | None) -> None:
    if dob is None:
        return
    age_years = (today_local() - dob).days / 365.25
    if not 3 <= age_years <= 25:
        raise ValueError("Date of birth must give an age between 3 and 25 years")


class StudentIn(InputModel):
    full_name: Name
    gender: Gender
    blood_group: BloodGroup | None = None
    date_of_birth: PastOrToday
    phone: OptionalPhone = None
    email: OptionalEmail = None
    address: OptionalText300 = None
    photo_file_id: uuid.UUID | None = None
    admission_number: OptStr30 = None
    admission_date: date
    category_id: uuid.UUID
    program_type_id: uuid.UUID
    training_center_id: uuid.UUID
    batch: Batch
    monthly_fee: MoneyIn
    remarks: OptionalText500 = None
    parent_name: Name
    parent_relationship: Relationship
    parent_phone: Phone
    parent_email: OptionalEmail = None
    parent_address: OptionalText300 = None
    emergency_name: OptionalShort = None
    emergency_relationship: OptStr40 = None
    emergency_phone: OptionalPhone = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_dob(self.date_of_birth)
        if self.admission_date < self.date_of_birth:
            raise ValueError("Admission date cannot be before date of birth")
        return self


class StudentPatch(InputModel):
    full_name: Name | None = None
    gender: Gender | None = None
    blood_group: BloodGroup | None = None
    date_of_birth: PastOrToday | None = None
    phone: OptionalPhone = None
    email: OptionalEmail = None
    address: OptionalText300 = None
    photo_file_id: uuid.UUID | None = None
    admission_number: OptStr30 = None
    admission_date: date | None = None
    category_id: uuid.UUID | None = None
    program_type_id: uuid.UUID | None = None
    training_center_id: uuid.UUID | None = None
    batch: Batch | None = None
    monthly_fee: MoneyIn | None = None
    status: RecordStatus | None = None
    remarks: OptionalText500 = None
    parent_name: Name | None = None
    parent_relationship: Relationship | None = None
    parent_phone: Phone | None = None
    parent_email: OptionalEmail = None
    parent_address: OptionalText300 = None
    emergency_name: OptionalShort = None
    emergency_relationship: OptStr40 = None
    emergency_phone: OptionalPhone = None

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_dob(self.date_of_birth)
        return self


class StudentListItem(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    photo: FileRef | None
    date_of_birth: date
    gender: Gender
    phone: str | None
    category: RefItem
    program_type: RefItem
    training_center: RefItem
    batch: Batch
    parent_name: str
    parent_phone: str
    status: RecordStatus
    monthly_fee: MoneyOut
    attendance_percentage: float | None = None
    current_month_fee_status: str | None = None
    fee_due_to_date: MoneyOut = Decimal(0)
    paid_to_date: MoneyOut = Decimal(0)
    outstanding: MoneyOut = Decimal(0)


class StudentTotals(CamelModel):
    present: int
    absent: int
    informed: int
    fee_due_to_date: MoneyOut
    paid_to_date: MoneyOut
    discount_to_date: MoneyOut
    outstanding: MoneyOut


class StudentDetail(StudentListItem):
    blood_group: BloodGroup | None
    email: str | None
    address: str | None
    admission_number: str
    admission_date: date
    remarks: str | None
    parent_relationship: Relationship
    parent_email: str | None
    parent_address: str | None
    emergency_name: str | None
    emergency_relationship: str | None
    emergency_phone: str | None
    totals: StudentTotals
    created_at: datetime
    updated_at: datetime


class FilterOptions(CamelModel):
    birth_years: list[int]
    categories: list[RefItem]
    program_types: list[RefItem]
    training_centers: list[RefItem]


class StudentAttendanceItem(CamelModel):
    session_id: uuid.UUID
    date: date
    status: AttendanceStatus
    remarks: str | None
    categories: list[str]
    coach_name: str


class StudentAttendanceOut(CamelModel):
    items: list[StudentAttendanceItem]
    summary: "AttendanceSummaryOut"


class AttendanceSummaryOut(CamelModel):
    total: int
    present: int
    absent: int
    informed: int
    rate: float | None


class ImportResult(CamelModel):
    created: int
    dry_run: bool


class StudentDocumentOut(CamelModel):
    id: uuid.UUID
    title: str
    file: FileRef
    uploaded_at: datetime
    uploaded_by: str | None


StudentAttendanceOut.model_rebuild()
