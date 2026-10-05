import uuid
from datetime import date, time
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import AttendanceStatus, pg_enum

if TYPE_CHECKING:
    from app.modules.coaches.models import CoachProfile
    from app.modules.reference.models import Category
    from app.modules.students.models import Student

session_categories = Table(
    "session_categories",
    Base.metadata,
    Column("session_id", ForeignKey("training_sessions.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="RESTRICT"), primary_key=True, index=True),
)


class TrainingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A daily training session report: plan, splits, and attendance (students + coaches)."""

    __tablename__ = "training_sessions"

    def __str__(self) -> str:
        return f"{self.session_date:%d %b %Y} · {self.daily_topic}"

    session_date: Mapped[date] = mapped_column(Date, index=True)
    created_by_coach_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coach_profiles.id", ondelete="RESTRICT"), index=True
    )
    venue: Mapped[str] = mapped_column(String(200))
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    weekly_topic: Mapped[str | None] = mapped_column(String(200))
    daily_topic: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    overview: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped["CoachProfile"] = relationship(lazy="selectin")
    categories: Mapped[list["Category"]] = relationship(secondary=session_categories, lazy="selectin")
    coaches: Mapped[list["SessionCoach"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="SessionCoach.role"
    )
    splits: Mapped[list["SessionSplit"]] = relationship(
        lazy="raise", cascade="all, delete-orphan", order_by="SessionSplit.position"
    )
    attendance: Mapped[list["StudentAttendance"]] = relationship(lazy="raise", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("end_time IS NULL OR start_time IS NULL OR end_time > start_time", name="time_order"),
        Index(
            "ix_training_sessions_daily_topic_trgm",
            "daily_topic",
            postgresql_using="gin",
            postgresql_ops={"daily_topic": "gin_trgm_ops"},
        ),
    )


class SessionCoachRole(StrEnum):
    CREATOR = "CREATOR"
    CO_COACH = "CO_COACH"


class SessionCoach(Base):
    __tablename__ = "session_coaches"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    coach_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coach_profiles.id", ondelete="RESTRICT"), primary_key=True
    )
    # Denormalised session date: the unique constraint below enforces one session per coach per day.
    session_date: Mapped[date] = mapped_column(Date)
    role: Mapped[SessionCoachRole] = mapped_column(pg_enum(SessionCoachRole, "session_coach_role"))
    status: Mapped[AttendanceStatus] = mapped_column(
        pg_enum(AttendanceStatus, "attendance_status"), default=AttendanceStatus.PRESENT
    )
    remarks: Mapped[str | None] = mapped_column(String(300))

    coach: Mapped["CoachProfile"] = relationship(lazy="selectin")

    __table_args__ = (UniqueConstraint("coach_id", "session_date", name="uq_session_coaches_coach_date"),)


class SessionSplit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "session_splits"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    heading: Mapped[str] = mapped_column(String(120))
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    explanation: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_session_splits_session_position"),
        CheckConstraint("duration_minutes BETWEEN 1 AND 300", name="duration_range"),
    )


class StudentAttendance(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "student_attendance"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("training_sessions.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"))
    session_date: Mapped[date] = mapped_column(Date)  # denormalised for monthly aggregation
    status: Mapped[AttendanceStatus] = mapped_column(pg_enum(AttendanceStatus, "attendance_status"))
    remarks: Mapped[str | None] = mapped_column(String(300))

    # Not auto-loaded: attendance streams already join the student; session detail loads it explicitly.
    student: Mapped["Student"] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_student_attendance_session_student"),
        Index("ix_student_attendance_student_date", "student_id", "session_date"),
        Index("ix_student_attendance_date_status", "session_date", "status"),
    )
