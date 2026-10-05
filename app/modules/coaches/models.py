import uuid
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, ForeignKey, SmallInteger, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import BloodGroup, Gender, pg_enum

if TYPE_CHECKING:
    from app.modules.files.models import StoredFile
    from app.modules.reference.models import Category
    from app.modules.users.models import User

coach_categories = Table(
    "coach_categories",
    Base.metadata,
    Column("coach_id", ForeignKey("coach_profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="RESTRICT"), primary_key=True, index=True),
)


class CoachProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Coach-specific data. The login, name, email and active flag live on `users`."""

    __tablename__ = "coach_profiles"

    def __str__(self) -> str:
        return f"Coach {self.user.full_name}"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    phone: Mapped[str] = mapped_column(String(10))
    gender: Mapped[Gender | None] = mapped_column(pg_enum(Gender, "gender"))
    blood_group: Mapped[BloodGroup | None] = mapped_column(pg_enum(BloodGroup, "blood_group"))
    experience_years: Mapped[int] = mapped_column(SmallInteger, default=0)
    joined_date: Mapped[date] = mapped_column(Date)
    address: Mapped[str | None] = mapped_column(String(300))
    bio: Mapped[str | None] = mapped_column(String(1000))
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )

    user: Mapped["User"] = relationship(lazy="selectin")
    photo: Mapped["StoredFile | None"] = relationship(lazy="selectin")
    categories: Mapped[list["Category"]] = relationship(
        secondary=coach_categories, lazy="selectin", order_by="Category.sort_order"
    )

    @property
    def full_name(self) -> str:
        return self.user.full_name


class DocumentKind(StrEnum):
    CONTRACT = "CONTRACT"
    GENERAL = "GENERAL"


class CoachDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "coach_documents"

    def __str__(self) -> str:
        return self.title

    coach_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("coach_profiles.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), unique=True
    )
    title: Mapped[str] = mapped_column(String(120))
    kind: Mapped[DocumentKind] = mapped_column(
        pg_enum(DocumentKind, "document_kind"), default=DocumentKind.GENERAL, server_default="GENERAL"
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    file: Mapped["StoredFile"] = relationship(lazy="selectin")
    uploaded_by: Mapped["User | None"] = relationship(lazy="selectin")
