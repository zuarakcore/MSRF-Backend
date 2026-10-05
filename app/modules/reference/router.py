"""Routes for categories, program types and training centers (one factory, three routers)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_admin
from app.core.enums import RecordStatus
from app.core.schemas import InputModel
from app.modules.reference import service
from app.modules.reference.models import Category, ProgramType, ReferenceBase, TrainingCenter
from app.modules.reference.schemas import (
    CategoryIn,
    CategoryPatch,
    RefOut,
    TrainingCenterIn,
    TrainingCenterPatch,
)


def build_router(
    prefix: str,
    model: type[ReferenceBase],
    create_schema: type[InputModel],
    patch_schema: type[InputModel],
    tag: str,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    label = service.LABELS[model].lower()

    @router.get("", response_model=list[RefOut], summary=f"List {label}s (any signed-in user)")
    async def list_items(
        _: CurrentUser,
        db: DbSession,
        search: Annotated[str | None, Query(max_length=100)] = None,
        status_filter: Annotated[RecordStatus | None, Query(alias="status")] = None,
    ) -> list[RefOut]:
        return await service.list_items(db, model, search=search, status=status_filter)

    @router.post(
        "",
        response_model=RefOut,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
        summary=f"Create a {label}",
    )
    async def create_item(body: create_schema, db: DbSession) -> RefOut:  # type: ignore[valid-type]
        return await service.create_item(db, model, body)

    @router.patch(
        "/{item_id}",
        response_model=RefOut,
        dependencies=[Depends(require_admin)],
        summary=f"Update a {label}",
    )
    async def update_item(item_id: uuid.UUID, body: patch_schema, db: DbSession) -> RefOut:  # type: ignore[valid-type]
        return await service.update_item(db, model, item_id, body)

    @router.delete(
        "/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
        summary=f"Delete a {label} (409 if in use; deactivate instead)",
    )
    async def delete_item(item_id: uuid.UUID, db: DbSession) -> Response:
        await service.delete_item(db, model, item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


categories_router = build_router("/categories", Category, CategoryIn, CategoryPatch, "Categories")
program_types_router = build_router("/program-types", ProgramType, CategoryIn, CategoryPatch, "Program Types")
training_centers_router = build_router(
    "/training-centers", TrainingCenter, TrainingCenterIn, TrainingCenterPatch, "Training Centers"
)
