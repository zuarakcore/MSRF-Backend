from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import RecordStatus, pg_enum


class ReferenceBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __abstract__ = True

    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[RecordStatus] = mapped_column(
        pg_enum(RecordStatus, "record_status"), default=RecordStatus.ACTIVE, server_default="ACTIVE"
    )
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class Category(ReferenceBase):
    """Student group, e.g. "Youth Football Squad (U-13)". Drives rosters and coach scope."""

    __tablename__ = "categories"
    description: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (Index("uq_categories_name_lower", text("lower(name)"), unique=True),)


class ProgramType(ReferenceBase):
    __tablename__ = "program_types"
    description: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (Index("uq_program_types_name_lower", text("lower(name)"), unique=True),)


class TrainingCenter(ReferenceBase):
    __tablename__ = "training_centers"
    location: Mapped[str] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(10))
    __table_args__ = (Index("uq_training_centers_name_lower", text("lower(name)"), unique=True),)


ReferenceModel = Category | ProgramType | TrainingCenter
