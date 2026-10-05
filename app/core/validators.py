"""Reusable Pydantic field types with the frontend's validation rules."""

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

from app.core.timeutils import today_local

_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def _normalise_phone(value: object) -> object:
    if not isinstance(value, str):
        return value
    digits = re.sub(r"[\s\-()]", "", value)
    for prefix in ("+91", "0091", "91", "0"):
        if digits.startswith(prefix) and len(digits) - len(prefix) == 10:
            digits = digits[len(prefix) :]
            break
    return digits


def _check_phone(value: str) -> str:
    if not _INDIAN_MOBILE.match(value):
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return value


def _empty_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


def _not_future(value: date) -> date:
    if value > today_local():
        raise ValueError("Date cannot be in the future")
    return value


# "+91 98470 12345", "098470-12345" -> "9847012345"
Phone = Annotated[str, BeforeValidator(_normalise_phone), AfterValidator(_check_phone)]
OptionalPhone = Annotated[Phone | None, BeforeValidator(_empty_to_none)]
PastOrToday = Annotated[date, AfterValidator(_not_future)]


Name = Annotated[str, Field(min_length=2, max_length=120)]
ShortText = Annotated[str, Field(min_length=1, max_length=120)]
Text2000 = Annotated[str, Field(min_length=1, max_length=2000)]
EmptyStrToNone = BeforeValidator(_empty_to_none)

# Optional free text: "" becomes None; the length limit applies to the string branch only
# (a constraint on `str | None` as a whole would also be applied to None and fail).
OptStr30 = Annotated[Annotated[str, Field(max_length=30)] | None, EmptyStrToNone]
OptStr40 = Annotated[Annotated[str, Field(max_length=40)] | None, EmptyStrToNone]
OptStr80 = Annotated[Annotated[str, Field(max_length=80)] | None, EmptyStrToNone]
OptStr120 = Annotated[Annotated[str, Field(max_length=120)] | None, EmptyStrToNone]
OptStr300 = Annotated[Annotated[str, Field(max_length=300)] | None, EmptyStrToNone]
OptStr500 = Annotated[Annotated[str, Field(max_length=500)] | None, EmptyStrToNone]
OptStr1000 = Annotated[Annotated[str, Field(max_length=1000)] | None, EmptyStrToNone]
OptStr2000 = Annotated[Annotated[str, Field(max_length=2000)] | None, EmptyStrToNone]
OptStr2048 = Annotated[Annotated[str, Field(max_length=2048)] | None, EmptyStrToNone]

# Backwards-compatible names used across schemas
OptionalShort = OptStr120
OptionalText300 = OptStr300
OptionalText500 = OptStr500
OptionalText1000 = OptStr1000
OptionalText2000 = OptStr2000
