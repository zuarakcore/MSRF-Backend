import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import EmailStr, Field, model_validator

from app.core.schemas import CamelModel, InputModel
from app.modules.users.models import Role

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128

NewPassword = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class UserOut(CamelModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    coach_id: uuid.UUID | None = None
    last_login_at: datetime | None


class TokenOut(CamelModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type
    expires_in: int
    user: UserOut


class LoginIn(InputModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class ForgotPasswordIn(InputModel):
    email: EmailStr


class ResetPasswordIn(InputModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = NewPassword


class ChangePasswordIn(InputModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = NewPassword

    @model_validator(mode="after")
    def _differs(self) -> Self:
        if self.current_password == self.new_password:
            raise ValueError("New password must differ from the current password")
        return self
