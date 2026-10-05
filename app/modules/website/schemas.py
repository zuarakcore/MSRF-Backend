import uuid
from datetime import date, datetime
from typing import Annotated, Self

from fastapi import UploadFile
from pydantic import AfterValidator, EmailStr, Field, model_validator

from app.core.enums import RecordStatus
from app.core.schemas import CamelModel, InputModel
from app.core.validators import (
    EmptyStrToNone,
    Name,
    OptionalPhone,
    OptionalText300,
    OptionalText500,
    OptStr80,
    OptStr120,
    OptStr2048,
    Phone,
)
from app.modules.files.schemas import FileRef, Photo
from app.modules.website.models import ApplicationStatus, EnquiryStatus, JobStatus

Label80 = OptStr80
dt_date = date


# --- programmes ----------------------------------------------------------------------------


def _clean_benefits(values: list[str]) -> list[str]:
    cleaned = [v.strip() for v in values if v and v.strip()]
    if any(len(v) > 120 for v in cleaned):
        raise ValueError("Each benefit must be at most 120 characters")
    return cleaned


Benefits = Annotated[list[str], AfterValidator(_clean_benefits), Field(max_length=10)]


class ProgrammeIn(InputModel):
    title: str = Field(min_length=2, max_length=120)
    age_group: OptStr80 = None
    description: str = Field(min_length=1, max_length=1000)
    duration: Label80 = None
    training_days: Label80 = None
    coach_label: Label80 = None
    benefits: Annotated[Benefits, Field(default_factory=list)]
    date: dt_date | None = None
    place: OptionalText300 = None
    kind: Label80 = None
    registration_url: OptStr2048 = None
    status: RecordStatus = RecordStatus.ACTIVE
    sort_order: int = Field(default=0, ge=0, le=10_000)


class ProgrammePatch(InputModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    age_group: OptStr80 = None
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    duration: Label80 = None
    training_days: Label80 = None
    coach_label: Label80 = None
    benefits: Benefits | None = None
    date: dt_date | None = None
    place: OptionalText300 = None
    kind: Label80 = None
    registration_url: OptStr2048 = None
    status: RecordStatus | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class ProgrammeOut(CamelModel):
    id: uuid.UUID
    slug: str
    title: str
    age_group: str | None = None
    description: str
    duration: str | None
    training_days: str | None
    coach_label: str | None
    benefits: list[str]
    date: dt_date | None = None
    place: str | None = None
    kind: str | None = None
    registration_url: str | None = None
    status: RecordStatus
    sort_order: int
    enquiries_count: int = 0
    created_at: datetime
    updated_at: datetime


class PublicProgramme(CamelModel):
    id: uuid.UUID
    slug: str
    title: str
    age_group: str | None = None
    description: str
    duration: str | None
    training_days: str | None
    coach_label: str | None
    benefits: list[str]
    date: dt_date | None = None
    place: str | None = None
    kind: str | None = None
    registration_url: str | None = None


class OrderIn(InputModel):
    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=500)]


# --- team ----------------------------------------------------------------------------------


class TeamMemberIn(InputModel):
    name: Name
    designation: str = Field(min_length=1, max_length=60)
    biography: OptionalText500 = None
    photo_file_id: uuid.UUID | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    sort_order: int = Field(default=0, ge=0, le=10_000)


class TeamMemberPatch(InputModel):
    name: Name | None = None
    designation: str | None = Field(default=None, min_length=1, max_length=60)
    biography: OptionalText500 = None
    photo_file_id: uuid.UUID | None = None
    status: RecordStatus | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class TeamMemberOut(CamelModel):
    id: uuid.UUID
    name: str
    designation: str
    biography: str | None
    photo: Photo
    status: RecordStatus
    sort_order: int
    created_at: datetime


class PublicTeamMember(CamelModel):
    id: uuid.UUID
    name: str
    designation: str
    biography: str | None
    photo_url: str


# --- gallery -------------------------------------------------------------------------------


class GalleryPatch(InputModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    caption: OptionalText300 = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    is_wide: bool | None = None
    status: RecordStatus | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class GalleryItemOut(CamelModel):
    id: uuid.UUID
    title: str
    caption: str | None
    category: str
    is_wide: bool
    image: FileRef
    thumbnail: FileRef | None
    status: RecordStatus
    sort_order: int
    created_at: datetime


class PublicGalleryItem(CamelModel):
    id: uuid.UUID
    title: str
    caption: str | None
    category: str
    is_wide: bool
    image_url: str
    thumbnail_url: str
    width: int | None
    height: int | None


# --- jobs ----------------------------------------------------------------------------------


class JobIn(InputModel):
    title: str = Field(min_length=2, max_length=120)
    location: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    experience_required: OptStr120 = None
    posted_on: date
    closing_date: date | None = None
    status: JobStatus = JobStatus.OPEN

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.closing_date and self.closing_date < self.posted_on:
            raise ValueError("Closing date must be on or after the posted date")
        return self


class JobPatch(InputModel):
    title: str | None = Field(default=None, min_length=2, max_length=120)
    location: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    experience_required: OptStr120 = None
    posted_on: date | None = None
    closing_date: date | None = None
    status: JobStatus | None = None


class JobOut(CamelModel):
    id: uuid.UUID
    title: str
    location: str
    description: str
    experience_required: str | None
    posted_on: date
    closing_date: date | None
    status: JobStatus
    applications_count: int = 0
    created_at: datetime


class PublicJob(CamelModel):
    id: uuid.UUID
    title: str
    location: str
    description: str
    experience_required: str | None
    posted_on: date
    closing_date: date | None


class ApplicationForm(InputModel):
    full_name: Name
    mobile_number: Phone
    email_address: EmailStr
    location: str = Field(min_length=2, max_length=100)
    captcha_token: OptStr2048 = None
    cv: UploadFile


class ApplicationPatch(InputModel):
    status: ApplicationStatus | None = None
    full_name: Name | None = None
    email: EmailStr | None = None
    phone: Phone | None = None


class ApplicationOut(CamelModel):
    id: uuid.UUID
    job_id: uuid.UUID
    position: str
    full_name: str
    email: str
    phone: str
    location: str
    cv: FileRef
    status: ApplicationStatus
    created_at: datetime


# --- enquiries ---------------------------------------------------------------------------


class EnquiryIn(InputModel):
    parent_name: Name
    player_name: OptStr120 = None
    phone: Phone
    email: Annotated[EmailStr | None, EmptyStrToNone] = None
    programme_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=2000)
    captcha_token: OptStr2048 = None


class EnquiryPatch(InputModel):
    status: EnquiryStatus | None = None
    parent_name: Name | None = None
    email: Annotated[EmailStr | None, EmptyStrToNone] = None
    phone: OptionalPhone = None


class EnquiryOut(CamelModel):
    id: uuid.UUID
    parent_name: str
    player_name: str | None
    phone: str
    email: str | None
    programme_id: uuid.UUID | None
    subject: str | None
    message: str
    status: EnquiryStatus
    created_at: datetime
