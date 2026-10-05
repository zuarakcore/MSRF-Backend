"""Reusable pagination: `PageParams` query dependency and the `Page[T]` response."""

import math
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import CamelModel

MAX_PAGE_SIZE = 100


class PageParams(CamelModel):
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


Pagination = Annotated[PageParams, Depends(_page_params)]


class Page[T](CamelModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


async def paginate_scalars(db: AsyncSession, stmt: Select[Any], params: PageParams) -> tuple[list[Any], int]:
    """Run a filtered select with LIMIT/OFFSET and return (rows, total)."""
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.scalars(stmt.limit(params.page_size).offset(params.offset))).all()
    return list(rows), total


def build_page[T](items: list[T], total: int, params: PageParams) -> Page[T]:
    return Page[T](
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
        pages=max(1, math.ceil(total / params.page_size)),
    )
