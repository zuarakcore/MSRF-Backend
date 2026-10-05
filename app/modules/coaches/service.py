import uuid
from datetime import timedelta
from typing import Any

from fastapi import UploadFile
from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AttendanceStatus, RecordStatus
from app.core.errors import BusinessRuleViolation, Conflict, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import period_bounds, today_local, utcnow
from app.modules.audit import service as audit
from app.modules.auth import service as auth_service
from app.modules.auth.models import AuthToken, AuthTokenPurpose, RefreshToken
from app.modules.coaches.models import CoachDocument, CoachProfile, DocumentKind, coach_categories
from app.modules.coaches.schemas import (
    AttendanceSummary,
    CoachAttendanceItem,
    CoachAttendanceOut,
    CoachDetail,
    CoachIn,
    CoachListItem,
    CoachPatch,
    DocumentOut,
    InviteStatus,
)
from app.modules.files import service as files
from app.modules.files.models import FilePurpose
from app.modules.performance.models import PerformanceReport
from app.modules.reference.models import Category
from app.modules.reference.schemas import RefItem
from app.modules.sessions.models import SessionCoach, TrainingSession
from app.modules.students.models import Student
from app.modules.users.models import Role, User

ATTENDANCE_WINDOW_DAYS = 90
_PROFILE_FIELDS = ("phone", "gender", "blood_group", "experience_years", "joined_date", "address", "bio")


async def get_coach(db: AsyncSession, coach_id: uuid.UUID) -> CoachProfile:
    coach = await db.get(CoachProfile, coach_id)
    if coach is None:
        raise NotFound("Coach not found", code="COACH_NOT_FOUND")
    return coach


async def category_ids_of(db: AsyncSession, coach_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(coach_categories.c.category_id).where(coach_categories.c.coach_id == coach_id)
    )
    return set(rows)


async def _resolve_categories(db: AsyncSession, ids: list[uuid.UUID]) -> list[Category]:
    unique = set(ids)
    if not unique:
        return []
    categories = list(await db.scalars(select(Category).where(Category.id.in_(unique))))
    if len(categories) != len(unique) or any(c.status is not RecordStatus.ACTIVE for c in categories):
        raise BusinessRuleViolation(
            "One or more categories are missing or inactive", code="REFERENCE_INACTIVE"
        )
    return categories


async def _invite_statuses(db: AsyncSession, users: list[User]) -> dict[uuid.UUID, InviteStatus]:
    pending_ids = [u.id for u in users if not u.is_verified]
    live: set[uuid.UUID] = set()
    if pending_ids:
        live = set(
            await db.scalars(
                select(AuthToken.user_id).where(
                    AuthToken.user_id.in_(pending_ids),
                    AuthToken.purpose == AuthTokenPurpose.INVITE,
                    AuthToken.used_at.is_(None),
                    AuthToken.expires_at > utcnow(),
                )
            )
        )
    return {
        u.id: InviteStatus.ACCEPTED
        if u.is_verified
        else (InviteStatus.PENDING if u.id in live else InviteStatus.EXPIRED)
        for u in users
    }


async def _stats(db: AsyncSession, coach_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, float | None]]:
    """(active student count in the coach's categories, attendance rate over the last 90 days)."""
    if not coach_ids:
        return {}
    students: dict[uuid.UUID, int] = dict(
        (
            await db.execute(
                select(coach_categories.c.coach_id, func.count(Student.id))
                .join(Student, Student.category_id == coach_categories.c.category_id)
                .where(coach_categories.c.coach_id.in_(coach_ids), Student.status == RecordStatus.ACTIVE)
                .group_by(coach_categories.c.coach_id)
            )
        ).all()
    )
    since = today_local() - timedelta(days=ATTENDANCE_WINDOW_DAYS)
    present = func.count().filter(SessionCoach.status == AttendanceStatus.PRESENT)
    rates = {
        coach_id: (round(100 * p / total, 1) if total else None)
        for coach_id, p, total in (
            await db.execute(
                select(SessionCoach.coach_id, present, func.count())
                .where(SessionCoach.coach_id.in_(coach_ids), SessionCoach.session_date >= since)
                .group_by(SessionCoach.coach_id)
            )
        ).all()
    }
    return {cid: (int(students.get(cid, 0)), rates.get(cid)) for cid in coach_ids}


async def _to_items(db: AsyncSession, coaches: list[CoachProfile], detail: bool = False) -> list[Any]:
    invites = await _invite_statuses(db, [c.user for c in coaches])
    stats = await _stats(db, [c.id for c in coaches])
    out: list[Any] = []
    for c in coaches:
        base: dict[str, Any] = {
            "id": c.id,
            "user_id": c.user_id,
            "full_name": c.user.full_name,
            "email": c.user.email,
            "phone": c.phone,
            "photo": c.photo,
            "experience_years": c.experience_years,
            "joined_date": c.joined_date,
            "status": RecordStatus.ACTIVE if c.user.is_active else RecordStatus.INACTIVE,
            "invite_status": invites[c.user_id],
            "categories": [RefItem.model_validate(cat) for cat in c.categories],
            "student_count": stats[c.id][0],
            "attendance_rate": stats[c.id][1],
        }
        if detail:
            base |= {
                "gender": c.gender,
                "blood_group": c.blood_group,
                "address": c.address,
                "bio": c.bio,
                "last_login_at": c.user.last_login_at,
                "created_at": c.created_at,
            }
            out.append(CoachDetail.model_validate(base))
        else:
            out.append(CoachListItem.model_validate(base))
    return out


async def list_coaches(
    db: AsyncSession,
    params: PageParams,
    *,
    search: str | None,
    status: RecordStatus | None,
    category_id: uuid.UUID | None,
) -> tuple[list[CoachListItem], int]:
    stmt: Select[Any] = (
        select(CoachProfile).join(User, User.id == CoachProfile.user_id).order_by(User.full_name)
    )
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(User.full_name.ilike(like), User.email.ilike(like), CoachProfile.phone.ilike(like))
        )
    if status:
        stmt = stmt.where(User.is_active.is_(status is RecordStatus.ACTIVE))
    if category_id:
        stmt = stmt.where(
            CoachProfile.id.in_(
                select(coach_categories.c.coach_id).where(coach_categories.c.category_id == category_id)
            )
        )
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    coaches = list(await db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return await _to_items(db, coaches), total


async def coach_detail(db: AsyncSession, coach: CoachProfile) -> CoachDetail:
    item: CoachDetail = (await _to_items(db, [coach], detail=True))[0]
    return item


async def create_coach(db: AsyncSession, data: CoachIn, actor: User) -> CoachDetail:
    email = data.email.lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("A user with this email already exists", code="EMAIL_EXISTS")
    photo = await files.get_file_of_purpose(db, data.photo_file_id, FilePurpose.COACH_PHOTO)
    categories = await _resolve_categories(db, data.category_ids)

    user = User(email=email, full_name=data.full_name, role=Role.COACH, password_hash=None)
    db.add(user)
    await db.flush()
    coach = CoachProfile(
        user_id=user.id,
        photo_file_id=photo.id if photo else None,
        **data.model_dump(include=set(_PROFILE_FIELDS)),
    )
    coach.categories = categories
    db.add(coach)
    await db.flush()
    audit.record(db, "COACH_CREATED", actor_user_id=actor.id, entity_type="coach", entity_id=coach.id)
    await auth_service.send_invite(db, user)  # commits, then queues the invite email
    await db.refresh(coach)
    return await coach_detail(db, coach)


async def _deactivate_sessions(db: AsyncSession, user: User) -> None:
    user.token_version += 1
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def update_coach(db: AsyncSession, coach_id: uuid.UUID, data: CoachPatch, actor: User) -> CoachDetail:
    coach = await get_coach(db, coach_id)
    user = coach.user
    values = data.model_dump(exclude_unset=True)

    if (email := values.pop("email", None)) and email.lower() != user.email:
        if await db.scalar(select(User.id).where(User.email == email.lower(), User.id != user.id)):
            raise Conflict("A user with this email already exists", code="EMAIL_EXISTS")
        user.email = email.lower()
    if (name := values.pop("full_name", None)) is not None:
        user.full_name = name
    if (status := values.pop("status", None)) is not None:
        active = status is RecordStatus.ACTIVE
        if user.is_active and not active:
            await _deactivate_sessions(db, user)  # takes effect on the coach's very next request
            audit.record(
                db, "COACH_DEACTIVATED", actor_user_id=actor.id, entity_type="coach", entity_id=coach.id
            )
        user.is_active = active
    if "category_ids" in values:
        coach.categories = await _resolve_categories(db, values.pop("category_ids") or [])
    if "photo_file_id" in values:
        photo = await files.get_file_of_purpose(db, values.pop("photo_file_id"), FilePurpose.COACH_PHOTO)
        coach.photo_file_id = photo.id if photo else None
    for field, value in values.items():
        if value is None and field in {"phone", "experience_years", "joined_date"}:
            continue
        setattr(coach, field, value)

    await db.commit()
    await db.refresh(coach)
    return await coach_detail(db, coach)


async def delete_coach(db: AsyncSession, coach_id: uuid.UUID, actor: User) -> None:
    coach = await get_coach(db, coach_id)
    has_history = await db.scalar(
        select(
            select(SessionCoach.session_id).where(SessionCoach.coach_id == coach_id).exists()
            | select(PerformanceReport.id).where(PerformanceReport.coach_id == coach_id).exists()
        )
    )
    if has_history:
        raise Conflict(
            "This coach has sessions or reports on record. Deactivate instead.", code="COACH_HAS_HISTORY"
        )
    documents = list(await db.scalars(select(CoachDocument).where(CoachDocument.coach_id == coach_id)))
    pending = [await files.delete_stored_file(db, d.file) for d in documents]
    for d in documents:
        await db.delete(d)
    user = coach.user
    audit.record(db, "COACH_DELETED", actor_user_id=actor.id, entity_type="coach", entity_id=coach.id)
    await db.delete(coach)
    await db.delete(user)
    await db.commit()
    await files.purge_objects(pending)


async def resend_invite(db: AsyncSession, coach_id: uuid.UUID) -> None:
    coach = await get_coach(db, coach_id)
    if coach.user.is_verified:
        raise Conflict("This coach has already set a password", code="INVITE_ALREADY_ACCEPTED")
    if not coach.user.is_active:
        raise BusinessRuleViolation("Activate the coach before sending an invite", code="ACCOUNT_INACTIVE")
    await auth_service.send_invite(db, coach.user)


async def coach_attendance(
    db: AsyncSession, coach_id: uuid.UUID, *, year: int | None, month: int | None
) -> CoachAttendanceOut:
    await get_coach(db, coach_id)
    stmt = (
        select(SessionCoach, TrainingSession)
        .join(TrainingSession, TrainingSession.id == SessionCoach.session_id)
        .where(SessionCoach.coach_id == coach_id)
        .order_by(SessionCoach.session_date.desc())
    )
    if year:
        start, end = period_bounds(year, month)
        stmt = stmt.where(SessionCoach.session_date >= start, SessionCoach.session_date < end)
    rows = (await db.execute(stmt.limit(500))).all()
    items = [
        CoachAttendanceItem(
            session_id=s.id,
            date=sc.session_date,
            venue=s.venue,
            daily_topic=s.daily_topic,
            role=sc.role.value,
            status=sc.status,
            remarks=sc.remarks,
        )
        for sc, s in rows
    ]
    return CoachAttendanceOut(items=items, summary=summarize([i.status for i in items]))


def summarize(statuses: list[AttendanceStatus]) -> AttendanceSummary:
    total = len(statuses)
    present = statuses.count(AttendanceStatus.PRESENT)
    return AttendanceSummary(
        total=total,
        present=present,
        absent=statuses.count(AttendanceStatus.ABSENT),
        informed=statuses.count(AttendanceStatus.INFORMED),
        rate=round(100 * present / total, 1) if total else None,
    )


# --- documents -----------------------------------------------------------------------


def document_out(doc: CoachDocument) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        kind=doc.kind,
        file=doc.file,
        uploaded_at=doc.created_at,
        uploaded_by=doc.uploaded_by.full_name if doc.uploaded_by else None,
    )


async def list_documents(db: AsyncSession, coach_id: uuid.UUID) -> list[DocumentOut]:
    await get_coach(db, coach_id)
    docs = await db.scalars(
        select(CoachDocument)
        .where(CoachDocument.coach_id == coach_id)
        .order_by(CoachDocument.created_at.desc())
    )
    return [document_out(d) for d in docs]


async def add_document(
    db: AsyncSession, coach_id: uuid.UUID, title: str, kind: DocumentKind, upload: UploadFile, actor: User
) -> DocumentOut:
    await get_coach(db, coach_id)
    stored = await files.store_upload(db, upload, purpose=FilePurpose.COACH_DOCUMENT, uploaded_by_id=actor.id)
    doc = CoachDocument(coach_id=coach_id, file_id=stored.id, title=title, kind=kind, uploaded_by_id=actor.id)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return document_out(doc)


async def delete_document(db: AsyncSession, coach_id: uuid.UUID, document_id: uuid.UUID) -> None:
    doc = await db.scalar(
        select(CoachDocument).where(and_(CoachDocument.id == document_id, CoachDocument.coach_id == coach_id))
    )
    if doc is None:
        raise NotFound("Document not found", code="DOCUMENT_NOT_FOUND")
    stored = doc.file
    await db.delete(doc)
    await db.flush()
    pending = await files.delete_stored_file(db, stored)
    await db.commit()
    await files.purge_objects([pending])
