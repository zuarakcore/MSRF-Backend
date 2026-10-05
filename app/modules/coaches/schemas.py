import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import EmailStr, Field

from app.core.enums import AttendanceStatus, BloodGroup, Gender, RecordStatus
from app.core.schemas import CamelModel, InputModel
from app.core.validators import Name, OptionalText300, OptionalText1000, PastOrToday, Phone
from app.modules.coaches.models import DocumentKind
from app.modules.files.schemas import FileRef, Photo
from app.modules.reference.schemas import RefItem


class InviteStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"


class CoachIn(InputModel):
    full_name: Name
    email: EmailStr
    phone: Phone
    gender: Gender | None = None
    blood_group: BloodGroup | None = None
    experience_years: int = Field(default=0, ge=0, le=60)
    joined_date: PastOrToday
    address: OptionalText300 = None
    bio: OptionalText1000 = None
    photo_file_id: uuid.UUID | None = None
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class CoachPatch(InputModel):
    full_name: Name | None = None
    email: EmailStr | None = None
    phone: Phone | None = None
    gender: Gender | None = None
    blood_group: BloodGroup | None = None
    experience_years: int | None = Field(default=None, ge=0, le=60)
    joined_date: PastOrToday | None = None
    address: OptionalText300 = None
    bio: OptionalText1000 = None
    photo_file_id: uuid.UUID | None = None
    category_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    status: RecordStatus | None = None


class CoachListItem(CamelModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str
    phone: str
    photo: Photo
    experience_years: int
    joined_date: date
    status: RecordStatus
    invite_status: InviteStatus
    categories: list[RefItem]
    student_count: int
    attendance_rate: float | None


class CoachDetail(CoachListItem):
    gender: Gender | None
    blood_group: BloodGroup | None
    address: str | None
    bio: str | None
    last_login_at: datetime | None
    created_at: datetime


class CoachAttendanceItem(CamelModel):
    session_id: uuid.UUID
    date: date
    venue: str
    daily_topic: str
    role: str
    status: AttendanceStatus
    remarks: str | None


class AttendanceSummary(CamelModel):
    total: int
    present: int
    absent: int
    informed: int
    rate: float | None


class CoachAttendanceOut(CamelModel):
    items: list[CoachAttendanceItem]
    summary: AttendanceSummary


class DocumentOut(CamelModel):
    id: uuid.UUID
    title: str
    kind: DocumentKind = DocumentKind.GENERAL
    file: FileRef
    uploaded_at: datetime
    uploaded_by: str | None
