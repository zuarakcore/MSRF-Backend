"""Enums shared by several modules, and a helper to map them to PostgreSQL enum types."""

from enum import StrEnum

from sqlalchemy import Enum


def pg_enum[E: StrEnum](enum_cls: type[E], name: str) -> Enum:
    """Store the enum *value* (e.g. "A+") rather than SQLAlchemy's default, the member name."""
    return Enum(
        enum_cls, name=name, values_callable=lambda cls: [m.value for m in cls], validate_strings=True
    )


class RecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Gender(StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class BloodGroup(StrEnum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"


class Batch(StrEnum):
    MORNING = "MORNING"
    EVENING = "EVENING"
    WEEKEND = "WEEKEND"


class Relationship(StrEnum):
    FATHER = "FATHER"
    MOTHER = "MOTHER"
    GUARDIAN = "GUARDIAN"
    OTHER = "OTHER"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    INFORMED = "INFORMED"


BATCH_LABELS = {
    Batch.MORNING: "Morning (6:00 AM - 8:00 AM)",
    Batch.EVENING: "Evening (4:00 PM - 6:00 PM)",
    Batch.WEEKEND: "Weekend Special",
}
