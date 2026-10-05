from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Role(StrEnum):
    ADMIN = "ADMIN"
    COACH = "COACH"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # NULL until a coach accepts their invite and sets a password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    is_verified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Embedded in access tokens; bumping it invalidates every outstanding access token.
    token_version: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_users_role_is_active", "role", "is_active"),)

    def __repr__(self) -> str:
        return f"<User {self.id} {self.role}>"
