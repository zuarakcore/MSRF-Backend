"""Base Pydantic models.

The API speaks camelCase (what the TypeScript frontends use); Python code uses snake_case.
`InputModel` forbids unknown fields, which blocks mass-assignment attempts such as
sending `role` or `isActive` in a body that does not declare them.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class InputModel(CamelModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MessageOut(CamelModel):
    detail: str


# Money leaves the API as a JSON number (rupees), not Pydantic's default string for Decimal.
MoneyOut = Annotated[Decimal, PlainSerializer(float, return_type=float)]
MoneyIn = Annotated[Decimal, Field(ge=0, le=1_000_000, max_digits=12, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, le=1_000_000, max_digits=12, decimal_places=2)]
