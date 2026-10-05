import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.core.enums import pg_enum


class NotificationType(StrEnum):
    PAYMENT = "PAYMENT"
    ADMISSION = "ADMISSION"
    FEE_DUE = "FEE_DUE"
    ENQUIRY = "ENQUIRY"
    APPLICATION = "APPLICATION"
    SYSTEM = "SYSTEM"


class Notification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[NotificationType] = mapped_column(pg_enum(NotificationType, "notification_type"))
    title: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(200))
    related_id: Mapped[uuid.UUID | None]
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )
