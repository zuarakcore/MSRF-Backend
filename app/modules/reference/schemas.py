import uuid
from datetime import datetime

from pydantic import Field

from app.core.enums import RecordStatus
from app.core.schemas import CamelModel, InputModel
from app.core.validators import OptionalPhone, OptionalText500


class RefItem(CamelModel):
    """Compact reference embedded in other responses."""

    id: uuid.UUID
    name: str


class _RefIn(InputModel):
    name: str = Field(min_length=2, max_length=100)
    status: RecordStatus = RecordStatus.ACTIVE
    sort_order: int = Field(default=0, ge=0, le=10_000)


class _RefPatch(InputModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    status: RecordStatus | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)


class CategoryIn(_RefIn):
    description: OptionalText500 = None


class CategoryPatch(_RefPatch):
    description: OptionalText500 = None


class TrainingCenterIn(_RefIn):
    location: str = Field(min_length=2, max_length=300)
    phone: OptionalPhone = None


class TrainingCenterPatch(_RefPatch):
    location: str | None = Field(default=None, min_length=2, max_length=300)
    phone: OptionalPhone = None


class RefOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    location: str | None = None
    phone: str | None = None
    status: RecordStatus
    sort_order: int
    student_count: int = 0
    created_at: datetime
    updated_at: datetime
