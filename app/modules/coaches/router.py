import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from app.api.deps import AdminUser, DbSession, require_admin
from app.core.enums import RecordStatus
from app.core.pagination import Page, Pagination, build_page
from app.core.rate_limit import enforce
from app.core.schemas import MessageOut
from app.modules.coaches import service
from app.modules.coaches.models import DocumentKind
from app.modules.coaches.schemas import (
    CoachAttendanceOut,
    CoachDetail,
    CoachIn,
    CoachListItem,
    CoachPatch,
    DocumentOut,
)

router = APIRouter(prefix="/coaches", tags=["Coaches"], dependencies=[Depends(require_admin)])

Year = Annotated[int | None, Query(ge=2020, le=2100)]
Month = Annotated[int | None, Query(ge=1, le=12)]


@router.get("", response_model=Page[CoachListItem], summary="List coaches")
async def list_coaches(
    db: DbSession,
    pagination: Pagination,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[RecordStatus | None, Query(alias="status")] = None,
    category_id: Annotated[uuid.UUID | None, Query(alias="categoryId")] = None,
) -> Page[CoachListItem]:
    items, total = await service.list_coaches(
        db, pagination, search=search, status=status_filter, category_id=category_id
    )
    return build_page(items, total, pagination)


@router.post(
    "",
    response_model=CoachDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a coach account and email an invite link (no passwords are generated)",
)
async def create_coach(body: CoachIn, db: DbSession, admin: AdminUser) -> CoachDetail:
    return await service.create_coach(db, body, admin)


@router.get("/{coach_id}", response_model=CoachDetail, summary="Coach profile")
async def get_coach(coach_id: uuid.UUID, db: DbSession) -> CoachDetail:
    return await service.coach_detail(db, await service.get_coach(db, coach_id))


@router.patch(
    "/{coach_id}",
    response_model=CoachDetail,
    summary="Update a coach; status INACTIVE signs the coach out everywhere immediately",
)
async def update_coach(coach_id: uuid.UUID, body: CoachPatch, db: DbSession, admin: AdminUser) -> CoachDetail:
    return await service.update_coach(db, coach_id, body, admin)


@router.delete(
    "/{coach_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a coach without history (409 otherwise; deactivate instead)",
)
async def delete_coach(coach_id: uuid.UUID, db: DbSession, admin: AdminUser) -> Response:
    await service.delete_coach(db, coach_id, admin)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{coach_id}/resend-invite",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MessageOut,
    summary="Email a fresh invite link (the previous one stops working)",
)
async def resend_invite(coach_id: uuid.UUID, db: DbSession) -> MessageOut:
    await enforce("resend-invite", "5/hour", str(coach_id))
    await service.resend_invite(db, coach_id)
    return MessageOut(detail="Invite sent")


@router.get("/{coach_id}/attendance", response_model=CoachAttendanceOut, summary="Coach attendance history")
async def coach_attendance(
    coach_id: uuid.UUID, db: DbSession, year: Year = None, month: Month = None
) -> CoachAttendanceOut:
    return await service.coach_attendance(db, coach_id, year=year, month=month)


@router.get("/{coach_id}/documents", response_model=list[DocumentOut], summary="Coach documents")
async def list_documents(coach_id: uuid.UUID, db: DbSession) -> list[DocumentOut]:
    return await service.list_documents(db, coach_id)


@router.post(
    "/{coach_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (PDF, DOC, DOCX, JPEG, PNG; max 10 MB)",
)
async def add_document(
    coach_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    title: Annotated[str, Form(min_length=1, max_length=120)],
    file: Annotated[UploadFile, File()],
    kind: Annotated[DocumentKind, Form()] = DocumentKind.GENERAL,
) -> DocumentOut:
    return await service.add_document(db, coach_id, title.strip(), kind, file, admin)


@router.delete(
    "/{coach_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a document"
)
async def delete_document(coach_id: uuid.UUID, document_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_document(db, coach_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
