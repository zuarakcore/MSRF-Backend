import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.pagination import Page, Pagination, build_page
from app.core.schemas import CamelModel
from app.modules.notifications import service
from app.modules.notifications.models import NotificationType

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationOut(CamelModel):
    id: uuid.UUID
    type: NotificationType
    title: str
    message: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class UnreadCount(CamelModel):
    count: int


@router.get("", response_model=Page[NotificationOut], summary="My notifications, newest first")
async def list_notifications(
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
) -> Page[NotificationOut]:
    rows, total = await service.list_for_user(db, user, pagination, unread_only=unread_only)
    return build_page([NotificationOut.model_validate(r) for r in rows], total, pagination)


@router.get(
    "/unread-count",
    response_model=UnreadCount,
    summary="Unread count for the header bell (poll every 60 s while the tab is visible)",
)
async def unread_count(user: CurrentUser, db: DbSession) -> UnreadCount:
    return UnreadCount(count=await service.unread_count(db, user))


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT, summary="Mark one as read")
async def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    await service.mark_read(db, user, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT, summary="Mark all as read")
async def mark_all_read(user: CurrentUser, db: DbSession) -> Response:
    await service.mark_all_read(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
