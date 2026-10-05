"""In-app notifications. `notify_admins` is the single place notifications are created
(the hook for adding push/SSE later: publish to Redis here)."""

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.pagination import PageParams, paginate_scalars
from app.core.timeutils import utcnow
from app.modules.notifications.models import Notification, NotificationType
from app.modules.users.models import Role, User


async def notify_admins(
    db: AsyncSession,
    type_: NotificationType,
    title: str,
    message: str,
    *,
    link: str | None = None,
    related_id: uuid.UUID | None = None,
    exclude_user_id: uuid.UUID | None = None,
) -> None:
    """One row per active admin, inserted in the caller's transaction (INSERT ... SELECT)."""
    admins = select(
        func.gen_random_uuid(),
        User.id,
        literal(type_.value).cast(Notification.__table__.c.type.type),
        literal(title[:120], String),
        literal(message[:500], String),
        literal(link, String),
        literal(related_id, Uuid),
    ).where(User.role == Role.ADMIN, User.is_active.is_(True))
    if exclude_user_id:
        admins = admins.where(User.id != exclude_user_id)
    await db.execute(
        insert(Notification).from_select(
            ["id", "user_id", "type", "title", "message", "link", "related_id"], admins
        )
    )
    try:
        from app.modules.websocket.manager import manager

        await manager.broadcast(
            {
                "type": "notification",
                "notificationType": type_.value,
                "title": title,
                "message": message,
                "link": link,
            }
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("WebSocket broadcast error: %s", exc)


async def list_for_user(
    db: AsyncSession, user: User, params: PageParams, *, unread_only: bool
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return await paginate_scalars(db, stmt.order_by(Notification.created_at.desc()), params)


async def unread_count(db: AsyncSession, user: User) -> int:
    return (
        await db.scalar(
            select(func.count()).where(Notification.user_id == user.id, Notification.read_at.is_(None))
        )
        or 0
    )


async def mark_read(db: AsyncSession, user: User, notification_id: uuid.UUID) -> None:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=func.coalesce(Notification.read_at, utcnow()))
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFound("Notification not found", code="NOTIFICATION_NOT_FOUND")
    await db.commit()


async def mark_all_read(db: AsyncSession, user: User) -> None:
    now: datetime = utcnow()
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=now)
    )
    await db.commit()
