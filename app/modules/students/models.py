import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import Batch, BloodGroup, Gender, RecordStatus, Relationship, pg_enum

if TYPE_CHECKING:
    from app.modules.files.models import StoredFile
    from app.modules.reference.models import Category, ProgramType, TrainingCenter
    from app.modules.users.models import User


class Student(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "students"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.student_code})"

    student_code: Mapped[str] = mapped_column(String(20), unique=True)
    admission_number: Mapped[str] = mapped_column(String(30), unique=True)
    admission_date: Mapped[date] = mapped_column(Date)

    full_name: Mapped[str] = mapped_column(String(120))
    date_of_birth: Mapped[date] = mapped_column(Date, index=True)
    gender: Mapped[Gender] = mapped_column(pg_enum(Gender, "gender"))
    blood_group: Mapped[BloodGroup | None] = mapped_column(pg_enum(BloodGroup, "blood_group"))
    phone: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(CITEXT)
    address: Mapped[str | None] = mapped_column(String(300))
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    program_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_types.id", ondelete="RESTRICT"), index=True
    )
    training_center_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_centers.id", ondelete="RESTRICT"), index=True
    )
    batch: Mapped[Batch] = mapped_column(pg_enum(Batch, "batch"))
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[RecordStatus] = mapped_column(
        pg_enum(RecordStatus, "record_status"), default=RecordStatus.ACTIVE, server_default="ACTIVE"
    )
    remarks: Mapped[str | None] = mapped_column(String(500))

    parent_name: Mapped[str] = mapped_column(String(120))
    parent_relationship: Mapped[Relationship] = mapped_column(pg_enum(Relationship, "relationship"))
    parent_phone: Mapped[str] = mapped_column(String(10), index=True)
    parent_email: Mapped[str | None] = mapped_column(CITEXT)
    parent_address: Mapped[str | None] = mapped_column(String(300))
    emergency_name: Mapped[str | None] = mapped_column(String(120))
    emergency_relationship: Mapped[str | None] = mapped_column(String(40))
    emergency_phone: Mapped[str | None] = mapped_column(String(10))

    # Many-to-one relations are small and always displayed: load them with the student.
    category: Mapped["Category"] = relationship(lazy="selectin")
    program_type: Mapped["ProgramType"] = relationship(lazy="selectin")
    training_center: Mapped["TrainingCenter"] = relationship(lazy="selectin")
    photo: Mapped["StoredFile | None"] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("monthly_fee >= 0", name="monthly_fee_non_negative"),
        # A student's own email is unique when given (citext: case-insensitive). parent_email is NOT
        # unique on purpose: siblings share a parent.
        Index("uq_students_email", "email", unique=True, postgresql_where=text("email IS NOT NULL")),
        Index("ix_students_status_category", "status", "category_id"),
        Index(
            "ix_students_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_students_parent_name_trgm",
            "parent_name",
            postgresql_using="gin",
            postgresql_ops={"parent_name": "gin_trgm_ops"},
        ),
    )


class StudentDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "student_documents"

    def __str__(self) -> str:
        return self.title

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), unique=True
    )
    title: Mapped[str] = mapped_column(String(120))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    file: Mapped["StoredFile"] = relationship(lazy="selectin")
    uploaded_by: Mapped["User | None"] = relationship(lazy="selectin")
