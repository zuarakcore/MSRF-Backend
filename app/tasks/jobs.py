"""Scheduled jobs. The logic lives in plain functions that take a Session, so it can be tested
and called from the CLI without Celery; the tasks are thin wrappers."""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, func, or_, select, text, union
from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.core.timeutils import month_start, today_local, utcnow
from app.modules.auth.models import AuthToken, RefreshToken
from app.modules.coaches.models import CoachProfile
from app.modules.fees import ledger
from app.modules.fees.models import FeeLedgerEntry
from app.modules.files.models import GENERIC_UPLOAD_PURPOSES, StoredFile
from app.modules.notifications.models import Notification, NotificationType
from app.modules.students.models import Student
from app.modules.users.models import Role, User
from app.modules.website.models import TeamMember

logger = logging.getLogger(__name__)

ORPHAN_GRACE_HOURS = 24
READ_NOTIFICATION_RETENTION_DAYS = 90


def generate_ledger(db: Session, period: date | None = None) -> int:
    """Create this month's fee entries for all active students. Safe to run repeatedly."""
    target = month_start(period or today_local())
    result = db.execute(ledger.generate_month_stmt(target))
    db.commit()
    created = int(result.rowcount or 0)  # type: ignore[attr-defined]
    logger.info("Fee ledger generated", extra={"period": target.isoformat(), "created": created})
    return created


def overdue_digest(db: Session) -> int:
    """Notify admins how many students are overdue (run after the due date)."""
    today = today_local()
    e = FeeLedgerEntry
    count = (
        db.scalar(
            select(func.count(func.distinct(e.student_id))).where(
                e.amount_paid + e.discount < e.amount_due, e.due_date < today
            )
        )
        or 0
    )
    if count:
        admins = db.scalars(select(User.id).where(User.role == Role.ADMIN, User.is_active.is_(True))).all()
        db.add_all(
            Notification(
                user_id=admin_id,
                type=NotificationType.FEE_DUE,
                title="Overdue fees",
                message=f"{count} student(s) have overdue fees.",
                link="/super-admin/fees",
            )
            for admin_id in admins
        )
        db.commit()
    return int(count)


def purge_orphan_uploads(db: Session) -> int:
    """Delete photos uploaded via POST /uploads that were never attached (or were replaced)."""
    cutoff = utcnow() - timedelta(hours=ORPHAN_GRACE_HOURS)
    referenced = union(
        select(Student.photo_file_id.label("fid")).where(Student.photo_file_id.is_not(None)),
        select(CoachProfile.photo_file_id).where(CoachProfile.photo_file_id.is_not(None)),
        select(TeamMember.photo_file_id).where(TeamMember.photo_file_id.is_not(None)),
    ).subquery()
    orphans = db.scalars(
        select(StoredFile).where(
            StoredFile.purpose.in_(GENERIC_UPLOAD_PURPOSES),
            StoredFile.created_at < cutoff,
            StoredFile.id.not_in(select(referenced.c.fid)),
        )
    ).all()
    storage = get_storage()
    for f in orphans:
        try:
            storage.delete(f.storage_key, f.visibility)
        except Exception as exc:
            logger.warning("Could not delete orphan object", extra={"key": f.storage_key, "error": str(exc)})
        db.delete(f)
    db.commit()
    return len(orphans)


def purge_expired_tokens(db: Session) -> int:
    now = utcnow()
    week_ago = now - timedelta(days=7)
    a = db.execute(
        delete(RefreshToken).where(
            or_(RefreshToken.expires_at < week_ago, RefreshToken.revoked_at < week_ago)
        )
    )
    b = db.execute(
        delete(AuthToken).where(or_(AuthToken.expires_at < week_ago, AuthToken.used_at < week_ago))
    )
    cutoff = now - timedelta(days=READ_NOTIFICATION_RETENTION_DAYS)
    c = db.execute(delete(Notification).where(Notification.read_at < cutoff))
    db.commit()
    return int((a.rowcount or 0) + (b.rowcount or 0) + (c.rowcount or 0))  # type: ignore[attr-defined]


def check_ledger_consistency(db: Session) -> int:
    """Assert amount_paid equals the sum of VALID allocations; log any drift (should be zero)."""
    rows = db.execute(
        text(
            """
            SELECT e.id FROM fee_ledger_entries e
            LEFT JOIN (
                SELECT a.ledger_entry_id, SUM(a.amount) AS total
                FROM payment_allocations a JOIN payments p ON p.id = a.payment_id
                WHERE p.status = 'VALID' GROUP BY a.ledger_entry_id
            ) s ON s.ledger_entry_id = e.id
            WHERE e.amount_paid <> COALESCE(s.total, 0)
            """
        )
    ).all()
    if rows:
        logger.error("Ledger drift detected", extra={"entries": [str(r[0]) for r in rows[:50]]})
    return len(rows)
