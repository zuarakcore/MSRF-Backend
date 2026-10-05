import datetime as dt
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ARRAY, CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import RecordStatus, pg_enum

if TYPE_CHECKING:
    from app.modules.files.models import StoredFile


class _Publishable:
    status: Mapped[RecordStatus] = mapped_column(
        pg_enum(RecordStatus, "record_status"), default=RecordStatus.ACTIVE, server_default="ACTIVE"
    )
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class Programme(UUIDPrimaryKeyMixin, TimestampMixin, _Publishable, Base):
    __tablename__ = "programmes"

    slug: Mapped[str] = mapped_column(String(140), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    age_group: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str] = mapped_column(String(1000))
    duration: Mapped[str | None] = mapped_column(String(80))
    training_days: Mapped[str | None] = mapped_column(String(80))
    coach_label: Mapped[str | None] = mapped_column(String(80))
    benefits: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list, server_default="{}")
    date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    place: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(60), nullable=True)
    registration_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TeamMember(UUIDPrimaryKeyMixin, TimestampMixin, _Publishable, Base):
    __tablename__ = "team_members"

    name: Mapped[str] = mapped_column(String(120))
    designation: Mapped[str] = mapped_column(String(60))
    biography: Mapped[str | None] = mapped_column(String(500))
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )

    photo: Mapped["StoredFile | None"] = relationship(lazy="selectin")


class GalleryItem(UUIDPrimaryKeyMixin, TimestampMixin, _Publishable, Base):
    __tablename__ = "gallery_items"

    title: Mapped[str] = mapped_column(String(120))
    caption: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(40), index=True)
    is_wide: Mapped[bool] = mapped_column(default=False, server_default="false")
    image_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stored_files.id", ondelete="RESTRICT"))
    thumbnail_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT")
    )

    image: Mapped["StoredFile"] = relationship(foreign_keys=[image_file_id], lazy="selectin")
    thumbnail: Mapped["StoredFile | None"] = relationship(foreign_keys=[thumbnail_file_id], lazy="selectin")


class JobStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    title: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    experience_required: Mapped[str | None] = mapped_column(String(120))
    posted_on: Mapped[dt.date] = mapped_column(Date)
    closing_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[JobStatus] = mapped_column(pg_enum(JobStatus, "job_status"), default=JobStatus.OPEN)

    __table_args__ = (
        CheckConstraint("closing_date IS NULL OR closing_date >= posted_on", name="closing_after_post"),
    )


class ApplicationStatus(StrEnum):
    UNDER_REVIEW = "UNDER_REVIEW"
    SHORTLISTED = "SHORTLISTED"
    REJECTED = "REJECTED"


class JobApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "job_applications"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(CITEXT)
    phone: Mapped[str] = mapped_column(String(10))
    location: Mapped[str] = mapped_column(String(100))
    cv_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stored_files.id", ondelete="RESTRICT"))
    status: Mapped[ApplicationStatus] = mapped_column(
        pg_enum(ApplicationStatus, "application_status"), default=ApplicationStatus.UNDER_REVIEW
    )
    submitter_ip: Mapped[str | None] = mapped_column(INET)

    job: Mapped[Job] = relationship(lazy="selectin")
    cv: Mapped["StoredFile"] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_job_applications_status_created", "status", "created_at"),)


class EnquiryStatus(StrEnum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    RESOLVED = "RESOLVED"


class Enquiry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "enquiries"

    parent_name: Mapped[str] = mapped_column(String(120))
    player_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(CITEXT)
    programme_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("programmes.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str | None] = mapped_column(String(120))  # programme title snapshot
    message: Mapped[str] = mapped_column(String(2000))
    status: Mapped[EnquiryStatus] = mapped_column(
        pg_enum(EnquiryStatus, "enquiry_status"), default=EnquiryStatus.NEW
    )
    submitter_ip: Mapped[str | None] = mapped_column(INET)

    programme: Mapped[Programme | None] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_enquiries_status_created", "status", "created_at"),)
