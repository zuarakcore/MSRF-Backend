import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import CHAR, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.core.enums import pg_enum
from app.core.storage import Visibility

MB = 1024 * 1024
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DOCUMENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
    }
)
RESUME_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


class FilePurpose(StrEnum):
    STUDENT_PHOTO = "STUDENT_PHOTO"
    STUDENT_DOCUMENT = "STUDENT_DOCUMENT"
    COACH_PHOTO = "COACH_PHOTO"
    COACH_DOCUMENT = "COACH_DOCUMENT"
    TEAM_PHOTO = "TEAM_PHOTO"
    GALLERY_IMAGE = "GALLERY_IMAGE"
    GALLERY_THUMBNAIL = "GALLERY_THUMBNAIL"
    PAYMENT_SCREENSHOT = "PAYMENT_SCREENSHOT"
    RESUME = "RESUME"


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    visibility: Visibility
    allowed_types: frozenset[str]
    max_bytes: int
    max_image_px: int = 2000


UPLOAD_POLICIES: dict[FilePurpose, UploadPolicy] = {
    # Photos of minors are private, never on a public URL.
    FilePurpose.STUDENT_PHOTO: UploadPolicy(Visibility.PRIVATE, IMAGE_TYPES, 5 * MB, 800),
    FilePurpose.STUDENT_DOCUMENT: UploadPolicy(Visibility.PRIVATE, DOCUMENT_TYPES, 10 * MB),
    FilePurpose.COACH_PHOTO: UploadPolicy(Visibility.PRIVATE, IMAGE_TYPES, 5 * MB, 800),
    FilePurpose.COACH_DOCUMENT: UploadPolicy(Visibility.PRIVATE, DOCUMENT_TYPES, 10 * MB),
    FilePurpose.TEAM_PHOTO: UploadPolicy(Visibility.PUBLIC, IMAGE_TYPES, 5 * MB, 800),
    FilePurpose.GALLERY_IMAGE: UploadPolicy(Visibility.PUBLIC, IMAGE_TYPES, 10 * MB, 2000),
    FilePurpose.GALLERY_THUMBNAIL: UploadPolicy(Visibility.PUBLIC, IMAGE_TYPES, 10 * MB, 600),
    FilePurpose.PAYMENT_SCREENSHOT: UploadPolicy(
        Visibility.PRIVATE, frozenset({"image/jpeg", "image/png"}), 5 * MB, 2000
    ),
    FilePurpose.RESUME: UploadPolicy(Visibility.PRIVATE, RESUME_TYPES, 5 * MB),
}

# Purposes an admin may upload through the generic two-step POST /uploads.
GENERIC_UPLOAD_PURPOSES = frozenset(
    {FilePurpose.STUDENT_PHOTO, FilePurpose.COACH_PHOTO, FilePurpose.TEAM_PHOTO}
)


class StoredFile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "stored_files"

    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    visibility: Mapped[Visibility] = mapped_column(pg_enum(Visibility, "file_visibility"))
    purpose: Mapped[FilePurpose] = mapped_column(pg_enum(FilePurpose, "file_purpose"))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    width: Mapped[int | None]
    height: Mapped[int | None]
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (Index("ix_stored_files_purpose_created_at", "purpose", "created_at"),)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")
