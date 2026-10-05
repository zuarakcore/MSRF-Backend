"""Generic CRUD for the three reference tables (categories, program types, training centers)."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.enums import RecordStatus
from app.core.errors import BusinessRuleViolation, Conflict, NotFound
from app.core.schemas import InputModel
from app.core.search import contains
from app.modules.reference.models import Category, ProgramType, ReferenceBase, TrainingCenter
from app.modules.reference.schemas import RefOut
from app.modules.students.models import Student

STUDENT_FK: dict[type[ReferenceBase], InstrumentedAttribute[uuid.UUID]] = {
    Category: Student.category_id,
    ProgramType: Student.program_type_id,
    TrainingCenter: Student.training_center_id,
}

LABELS: dict[type[ReferenceBase], str] = {
    Category: "Category",
    ProgramType: "Program type",
    TrainingCenter: "Training center",
}


def _code(model: type[ReferenceBase], suffix: str) -> str:
    return f"{LABELS[model].upper().replace(' ', '_')}_{suffix}"


async def list_items(
    db: AsyncSession, model: type[ReferenceBase], *, search: str | None, status: RecordStatus | None
) -> list[RefOut]:
    fk = STUDENT_FK[model]
    counts = (
        select(fk.label("ref_id"), func.count().label("n"))
        .where(Student.status == RecordStatus.ACTIVE)
        .group_by(fk)
        .subquery()
    )
    stmt = (
        select(model, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.ref_id == model.id)
        .order_by(model.sort_order, model.name)
        .limit(500)
    )
    if search:
        stmt = stmt.where(model.name.ilike(contains(search)))
    if status:
        stmt = stmt.where(model.status == status)
    rows = (await db.execute(stmt)).all()
    return [RefOut.model_validate(item).model_copy(update={"student_count": n}) for item, n in rows]


async def get_item(db: AsyncSession, model: type[ReferenceBase], item_id: uuid.UUID) -> ReferenceBase:
    item = await db.get(model, item_id)
    if item is None:
        raise NotFound(f"{LABELS[model]} not found", code=_code(model, "NOT_FOUND"))
    return item


async def _ensure_unique_name(
    db: AsyncSession, model: type[ReferenceBase], name: str, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(model.id).where(func.lower(model.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(model.id != exclude_id)
    if await db.scalar(stmt):
        raise Conflict(f"{LABELS[model]} '{name}' already exists", code="NAME_EXISTS")


async def create_item(db: AsyncSession, model: type[ReferenceBase], data: InputModel) -> RefOut:
    values: dict[str, Any] = data.model_dump()
    await _ensure_unique_name(db, model, values["name"])
    item = model(**values)
    db.add(item)
    await db.commit()
    return RefOut.model_validate(item)


async def update_item(
    db: AsyncSession, model: type[ReferenceBase], item_id: uuid.UUID, data: InputModel
) -> RefOut:
    item = await get_item(db, model, item_id)
    values = data.model_dump(exclude_unset=True)
    if values.get("name") is not None:
        await _ensure_unique_name(db, model, values["name"], exclude_id=item_id)
    for field, value in values.items():
        if value is None and field in {"name", "status", "sort_order", "location"}:
            continue  # required columns cannot be cleared
        setattr(item, field, value)
    await db.commit()
    await db.refresh(item)
    return RefOut.model_validate(item)


async def delete_item(db: AsyncSession, model: type[ReferenceBase], item_id: uuid.UUID) -> None:
    item = await get_item(db, model, item_id)
    in_use = await db.scalar(select(func.count()).where(STUDENT_FK[model] == item_id))
    if in_use:
        raise Conflict(
            f"{LABELS[model]} is used by {in_use} student(s). Deactivate it instead.", code="IN_USE"
        )
    await db.delete(item)
    await db.commit()  # other references (sessions, coaches) are caught by FK RESTRICT -> 409 IN_USE


async def require_active(db: AsyncSession, model: type[ReferenceBase], item_id: uuid.UUID) -> ReferenceBase:
    """For create/update of other records: the reference must exist and be ACTIVE."""
    item = await db.get(model, item_id)
    if item is None or item.status is not RecordStatus.ACTIVE:
        raise BusinessRuleViolation(f"{LABELS[model]} is missing or inactive", code="REFERENCE_INACTIVE")
    return item
