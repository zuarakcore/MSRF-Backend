import uuid
from datetime import date, datetime, time
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.enums import AttendanceStatus
from app.core.schemas import CamelModel, InputModel
from app.core.validators import OptionalText300, OptionalText2000
from app.modules.files.schemas import FileRef
from app.modules.reference.schemas import RefItem


class SplitIn(InputModel):
    heading: str = Field(min_length=1, max_length=120)
    duration_minutes: int = Field(ge=1, le=300)
    explanation: OptionalText2000 = None


class AttendanceIn(InputModel):
    student_id: uuid.UUID
    status: AttendanceStatus
    remarks: OptionalText300 = None


class SessionIn(InputModel):
    session_date: date
    category_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=20)]
    co_coach_ids: Annotated[list[uuid.UUID], Field(default_factory=list, max_length=20)]
    venue: str = Field(min_length=1, max_length=200)
    start_time: time | None = None
    end_time: time | None = None
    weekly_topic: Annotated[str | None, Field(max_length=200)] = None
    daily_topic: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=5000)
    overview: Annotated[str | None, Field(max_length=5000)] = None
    splits: Annotated[list[SplitIn], Field(min_length=1, max_length=20)]
    attendance: Annotated[list[AttendanceIn], Field(default_factory=list, max_length=500)]

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("End time must be after start time")
        ids = [a.student_id for a in self.attendance]
        if len(ids) != len(set(ids)):
            raise ValueError("A student appears more than once in attendance")
        return self


class SessionCoachOut(CamelModel):
    coach: RefItem
    role: str
    status: AttendanceStatus


class AttendanceCounts(CamelModel):
    present: int = 0
    absent: int = 0
    informed: int = 0
    total: int = 0


class SessionListItem(CamelModel):
    id: uuid.UUID
    session_date: date
    venue: str
    start_time: time | None
    end_time: time | None
    daily_topic: str
    categories: list[RefItem]
    created_by: RefItem
    coaches: list[SessionCoachOut]
    counts: AttendanceCounts
    can_edit: bool = False


class SplitOut(CamelModel):
    position: int
    heading: str
    duration_minutes: int
    explanation: str | None


class AttendanceStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    photo: FileRef | None


class AttendanceOut(CamelModel):
    student: AttendanceStudent
    status: AttendanceStatus
    remarks: str | None


class SessionDetail(SessionListItem):
    weekly_topic: str | None
    explanation: str
    overview: str | None
    splits: list[SplitOut]
    attendance: list[AttendanceOut]
    created_at: datetime
    updated_at: datetime


class RosterStudent(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    photo: FileRef | None
    category: RefItem
    batch: str


class RosterOut(CamelModel):
    students: list[RosterStudent]


class AttendanceStreamItem(CamelModel):
    id: uuid.UUID
    date: date
    session_id: uuid.UUID
    student: AttendanceStudent
    category: RefItem
    status: AttendanceStatus
    remarks: str | None
    marked_by: RefItem


class AttendanceSummaryItem(CamelModel):
    student: AttendanceStudent
    category: RefItem
    total_sessions: int
    present: int
    absent: int
    informed: int
    rate: float | None
    band: str


class CoachAttendanceStreamItem(CamelModel):
    date: date
    session_id: uuid.UUID
    coach: RefItem
    phone: str
    venue: str
    categories: list[str]
    role: str
    status: AttendanceStatus
    remarks: str | None
