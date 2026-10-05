"""Training sessions and attendance.

Coach rules (enforced here, not in the UI):
* A coach works only with students in the categories assigned to them.
* One session per coach per day, as creator or co-coach (also a DB unique constraint).
* Session date: today - 7 days .. today (Asia/Kolkata).
* Only the creator may edit/delete, and only within 7 days of the session date.
* Every coach on a session is recorded PRESENT (matches the current UI behaviour).
"""

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import AttendanceStatus, RecordStatus
from app.core.errors import INTEGRITY_ERROR_MAP, BusinessRuleViolation, Conflict, Forbidden, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import period_bounds, today_local
from app.modules.coaches.models import CoachProfile
from app.modules.coaches.service import category_ids_of
from app.modules.reference.models import Category
from app.modules.reference.schemas import RefItem
from app.modules.sessions.models import (
    SessionCoach,
    SessionCoachRole,
    SessionSplit,
    StudentAttendance,
    TrainingSession,
    session_categories,
)
from app.modules.sessions.schemas import (
    AttendanceCounts,
    AttendanceOut,
    AttendanceStreamItem,
    AttendanceStudent,
    AttendanceSummaryItem,
    CoachAttendanceStreamItem,
    RosterStudent,
    SessionCoachOut,
    SessionDetail,
    SessionIn,
    SessionListItem,
    SplitOut,
)
from app.modules.students.models import Student
from app.modules.users.models import User

EDIT_WINDOW_DAYS = 7

INTEGRITY_ERROR_MAP["uq_session_coaches_coach_date"] = (
    409,
    "SESSION_EXISTS_FOR_DATE",
    "A coach on this session already has a session on this date",
)


def coach_ref(coach: CoachProfile) -> RefItem:
    return RefItem(id=coach.id, name=coach.user.full_name)


def within_edit_window(day: date) -> bool:
    delta = (today_local() - day).days
    return 0 <= delta <= EDIT_WINDOW_DAYS


def _check_date(day: date) -> None:
    if not within_edit_window(day):
        raise BusinessRuleViolation(
            f"Session date must be between {EDIT_WINDOW_DAYS} days ago and today", code="DATE_OUT_OF_RANGE"
        )


# --- roster ------------------------------------------------------------------------------


async def _assigned_categories(
    db: AsyncSession, coach: CoachProfile, requested: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    allowed = await category_ids_of(db, coach.id)
    wanted = set(requested)
    if not wanted <= allowed:
        raise BusinessRuleViolation(
            "You can only work with categories assigned to you", code="CATEGORY_NOT_ASSIGNED"
        )
    return wanted


async def roster(
    db: AsyncSession, coach: CoachProfile, category_ids: Sequence[uuid.UUID]
) -> list[RosterStudent]:
    wanted = await _assigned_categories(db, coach, category_ids)
    students = await db.scalars(
        select(Student)
        .where(Student.category_id.in_(wanted), Student.status == RecordStatus.ACTIVE)
        .order_by(Student.full_name)
    )
    return [
        RosterStudent(
            id=s.id,
            student_code=s.student_code,
            full_name=s.full_name,
            photo=s.photo,
            category=RefItem.model_validate(s.category),
            batch=s.batch.value,
        )
        for s in students
    ]


# --- output ------------------------------------------------------------------------------


async def _counts(db: AsyncSession, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, AttendanceCounts]:
    if not session_ids:
        return {}
    rows = (
        await db.execute(
            select(StudentAttendance.session_id, StudentAttendance.status, func.count())
            .where(StudentAttendance.session_id.in_(session_ids))
            .group_by(StudentAttendance.session_id, StudentAttendance.status)
        )
    ).all()
    counts: dict[uuid.UUID, AttendanceCounts] = {sid: AttendanceCounts() for sid in session_ids}
    for sid, status, n in rows:
        c = counts[sid]
        setattr(c, status.value.lower(), n)
        c.total += n
    return counts


def _list_item(
    session: TrainingSession, counts: AttendanceCounts, viewer: CoachProfile | None
) -> dict[str, Any]:
    return {
        "id": session.id,
        "session_date": session.session_date,
        "venue": session.venue,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "daily_topic": session.daily_topic,
        "categories": [RefItem.model_validate(c) for c in session.categories],
        "created_by": coach_ref(session.created_by),
        "coaches": [
            SessionCoachOut(coach=coach_ref(sc.coach), role=sc.role.value, status=sc.status)
            for sc in session.coaches
        ],
        "counts": counts,
        "can_edit": viewer is not None
        and session.created_by_coach_id == viewer.id
        and within_edit_window(session.session_date),
    }


async def list_items(
    db: AsyncSession, sessions: list[TrainingSession], viewer: CoachProfile | None
) -> list[SessionListItem]:
    counts = await _counts(db, [s.id for s in sessions])
    return [SessionListItem.model_validate(_list_item(s, counts[s.id], viewer)) for s in sessions]


async def load_session(db: AsyncSession, session_id: uuid.UUID) -> TrainingSession | None:
    return await db.scalar(
        select(TrainingSession)
        .where(TrainingSession.id == session_id)
        .options(selectinload(TrainingSession.splits), selectinload(TrainingSession.attendance))
        .execution_options(populate_existing=True)
    )


async def session_detail(
    db: AsyncSession, session: TrainingSession, viewer: CoachProfile | None
) -> SessionDetail:
    counts = (await _counts(db, [session.id]))[session.id]
    attendance = sorted(session.attendance, key=lambda a: a.student.full_name)
    return SessionDetail.model_validate(
        _list_item(session, counts, viewer)
        | {
            "weekly_topic": session.weekly_topic,
            "explanation": session.explanation,
            "overview": session.overview,
            "splits": [SplitOut.model_validate(sp) for sp in session.splits],
            "attendance": [
                AttendanceOut(
                    student=AttendanceStudent.model_validate(a.student), status=a.status, remarks=a.remarks
                )
                for a in attendance
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
    )


# --- coach: queries ----------------------------------------------------------------------


def _filtered(
    stmt: Select[Any],
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    year: int | None = None,
    month: int | None = None,
    day: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[Any]:
    if search:
        like = contains(search)
        stmt = stmt.where(or_(TrainingSession.daily_topic.ilike(like), TrainingSession.venue.ilike(like)))
    if category_id:
        stmt = stmt.where(
            TrainingSession.id.in_(
                select(session_categories.c.session_id).where(session_categories.c.category_id == category_id)
            )
        )
    if year:
        start, end = period_bounds(year, month)
        stmt = stmt.where(TrainingSession.session_date >= start, TrainingSession.session_date < end)
    if day:
        stmt = stmt.where(TrainingSession.session_date == day)
    if date_from:
        stmt = stmt.where(TrainingSession.session_date >= date_from)
    if date_to:
        stmt = stmt.where(TrainingSession.session_date <= date_to)
    return stmt.order_by(TrainingSession.session_date.desc(), TrainingSession.created_at.desc())


def _coach_sessions(coach: CoachProfile) -> Select[Any]:
    return select(TrainingSession).where(
        TrainingSession.id.in_(select(SessionCoach.session_id).where(SessionCoach.coach_id == coach.id))
    )


async def list_for_coach(
    db: AsyncSession, coach: CoachProfile, params: PageParams, **filters: Any
) -> tuple[list[SessionListItem], int]:
    stmt = _filtered(_coach_sessions(coach), **filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    sessions = list(await db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return await list_items(db, sessions, coach), total


async def get_for_coach(db: AsyncSession, coach: CoachProfile, session_id: uuid.UUID) -> TrainingSession:
    session = await load_session(db, session_id)
    if session is None or coach.id not in {sc.coach_id for sc in session.coaches}:
        raise NotFound("Session not found", code="SESSION_NOT_FOUND")  # never confirm others' sessions exist
    return session


# --- coach: writes -----------------------------------------------------------------------


async def _validate(
    db: AsyncSession, coach: CoachProfile, data: SessionIn, exclude_session: uuid.UUID | None
) -> tuple[list[Category], list[CoachProfile], dict[uuid.UUID, Student]]:
    _check_date(data.session_date)
    wanted = await _assigned_categories(db, coach, data.category_ids)
    categories = list(await db.scalars(select(Category).where(Category.id.in_(wanted))))
    if any(c.status is not RecordStatus.ACTIVE for c in categories):
        raise BusinessRuleViolation("One or more categories are inactive", code="REFERENCE_INACTIVE")

    co_ids = set(data.co_coach_ids) - {coach.id}
    co_coaches: list[CoachProfile] = []
    if co_ids:
        co_coaches = list(
            await db.scalars(
                select(CoachProfile)
                .join(User, User.id == CoachProfile.user_id)
                .where(CoachProfile.id.in_(co_ids), User.is_active.is_(True))
            )
        )
        if len(co_coaches) != len(co_ids):
            raise BusinessRuleViolation(
                "One or more co-coaches are missing or inactive", code="COACH_NOT_FOUND"
            )

    all_coaches = [coach, *co_coaches]
    clash_stmt = select(SessionCoach).where(
        SessionCoach.coach_id.in_([c.id for c in all_coaches]), SessionCoach.session_date == data.session_date
    )
    if exclude_session:
        clash_stmt = clash_stmt.where(SessionCoach.session_id != exclude_session)
    clash = await db.scalar(clash_stmt.limit(1))
    if clash:
        raise Conflict(
            f"{clash.coach.user.full_name} already has a session on {data.session_date:%d %b %Y}. "
            "Only one session per coach per day is allowed.",
            code="SESSION_EXISTS_FOR_DATE",
        )

    student_ids = {a.student_id for a in data.attendance}
    students: dict[uuid.UUID, Student] = {}
    if student_ids:
        students = {
            s.id: s
            for s in await db.scalars(
                select(Student).where(
                    Student.id.in_(student_ids),
                    Student.category_id.in_(wanted),
                    Student.status == RecordStatus.ACTIVE,
                )
            )
        }
        if len(students) != len(student_ids):
            raise BusinessRuleViolation(
                "Attendance includes students outside the selected categories",
                code="STUDENT_NOT_IN_SESSION_CATEGORIES",
            )
    return categories, co_coaches, students


def _fill(
    session: TrainingSession,
    data: SessionIn,
    coach: CoachProfile,
    categories: list[Category],
    co_coaches: list[CoachProfile],
) -> list[Any]:
    """Set scalar fields and categories; return child rows to add (with explicit session_id).

    Children are added directly rather than through the collections, so an update never has to
    load (and diff) the old collections; those were already removed with explicit DELETEs.
    """
    for field in (
        "session_date",
        "venue",
        "start_time",
        "end_time",
        "weekly_topic",
        "daily_topic",
        "explanation",
        "overview",
    ):
        setattr(session, field, getattr(data, field))
    session.categories = categories
    sid, day = session.id, data.session_date
    children: list[Any] = [
        SessionCoach(session_id=sid, coach_id=coach.id, session_date=day, role=SessionCoachRole.CREATOR)
    ]
    children += [
        SessionCoach(session_id=sid, coach_id=c.id, session_date=day, role=SessionCoachRole.CO_COACH)
        for c in co_coaches
    ]
    children += [
        SessionSplit(
            session_id=sid,
            position=i,
            heading=sp.heading,
            duration_minutes=sp.duration_minutes,
            explanation=sp.explanation,
        )
        for i, sp in enumerate(data.splits, start=1)
    ]
    children += [
        StudentAttendance(
            session_id=sid, student_id=a.student_id, session_date=day, status=a.status, remarks=a.remarks
        )
        for a in data.attendance
    ]
    return children


async def create_session(db: AsyncSession, coach: CoachProfile, data: SessionIn) -> SessionDetail:
    categories, co_coaches, _ = await _validate(db, coach, data, exclude_session=None)
    session = TrainingSession(id=uuid.uuid4(), created_by_coach_id=coach.id)
    children = _fill(session, data, coach, categories, co_coaches)
    db.add(session)
    await db.flush()
    db.add_all(children)
    await db.commit()
    loaded = await load_session(db, session.id)
    assert loaded is not None
    return await session_detail(db, loaded, coach)


def _require_editable(session: TrainingSession, coach: CoachProfile) -> None:
    if session.created_by_coach_id != coach.id:
        raise Forbidden("Only the coach who created this session can change it", code="SESSION_LOCKED")
    if not within_edit_window(session.session_date):
        raise Forbidden(f"Sessions older than {EDIT_WINDOW_DAYS} days are locked", code="SESSION_LOCKED")


async def _clear_children(db: AsyncSession, session_id: uuid.UUID) -> None:
    for model in (SessionCoach, SessionSplit, StudentAttendance):
        await db.execute(delete(model).where(model.session_id == session_id))


async def update_session(
    db: AsyncSession, coach: CoachProfile, session_id: uuid.UUID, data: SessionIn
) -> SessionDetail:
    session = await get_for_coach(db, coach, session_id)
    _require_editable(session, coach)
    categories, co_coaches, _ = await _validate(db, coach, data, exclude_session=session_id)
    # Replace children with explicit deletes first: re-adding rows with the same unique keys
    # in one flush would otherwise conflict.
    await _clear_children(db, session_id)
    db.expire(session, ["coaches", "splits", "attendance"])
    db.add_all(_fill(session, data, coach, categories, co_coaches))
    await db.commit()
    loaded = await load_session(db, session_id)
    assert loaded is not None
    return await session_detail(db, loaded, coach)


async def delete_session(db: AsyncSession, coach: CoachProfile, session_id: uuid.UUID) -> None:
    session = await get_for_coach(db, coach, session_id)
    _require_editable(session, coach)
    await db.delete(session)
    await db.commit()


# --- admin -------------------------------------------------------------------------------


async def list_for_admin(
    db: AsyncSession, params: PageParams, *, coach_id: uuid.UUID | None = None, **filters: Any
) -> tuple[list[SessionListItem], int]:
    stmt = select(TrainingSession)
    if coach_id:
        stmt = stmt.where(
            TrainingSession.id.in_(select(SessionCoach.session_id).where(SessionCoach.coach_id == coach_id))
        )
    stmt = _filtered(stmt, **filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    sessions = list(await db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return await list_items(db, sessions, None), total


async def get_for_admin(db: AsyncSession, session_id: uuid.UUID) -> SessionDetail:
    session = await load_session(db, session_id)
    if session is None:
        raise NotFound("Session not found", code="SESSION_NOT_FOUND")
    return await session_detail(db, session, None)


def _period_filter(column: Any, year: int | None, month: int | None, day: date | None) -> list[Any]:
    conditions: list[Any] = []
    if year:
        start, end = period_bounds(year, month)
        conditions += [column >= start, column < end]
    if day:
        conditions.append(column == day)
    return conditions


def check_not_future(year: int | None, month: int | None) -> None:
    today = today_local()
    if year and (year > today.year or (year == today.year and month and month > today.month)):
        raise BusinessRuleViolation("The selected period is in the future", code="PERIOD_IN_FUTURE")


def student_attendance_query(
    *,
    year: int | None,
    month: int | None,
    day: date | None,
    category_id: uuid.UUID | None,
    status: AttendanceStatus | None,
    search: str | None,
) -> Select[*tuple[Any, ...]]:
    stmt: Select[*tuple[Any, ...]] = (
        select(StudentAttendance, TrainingSession, Student)
        .join(TrainingSession, TrainingSession.id == StudentAttendance.session_id)
        .join(Student, Student.id == StudentAttendance.student_id)
        .where(*_period_filter(StudentAttendance.session_date, year, month, day))
    )
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if status:
        stmt = stmt.where(StudentAttendance.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))
    return stmt.order_by(StudentAttendance.session_date.desc(), Student.full_name)


def stream_item(att: StudentAttendance, session: TrainingSession, student: Student) -> AttendanceStreamItem:
    return AttendanceStreamItem(
        id=att.id,
        date=att.session_date,
        session_id=session.id,
        student=AttendanceStudent.model_validate(student),
        category=RefItem.model_validate(student.category),
        status=att.status,
        remarks=att.remarks,
        marked_by=coach_ref(session.created_by),
    )


async def attendance_stream(
    db: AsyncSession, params: PageParams, **filters: Any
) -> tuple[list[AttendanceStreamItem], int]:
    stmt = student_attendance_query(**filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(stmt.limit(params.page_size).offset(params.offset))).all()
    return [stream_item(a, s, st) for a, s, st in rows], total


def band(rate: float | None) -> str:
    if rate is None:
        return "NO_DATA"
    return "GOOD" if rate >= 85 else "NEEDS_ATTENTION" if rate >= 70 else "CRITICAL"


async def attendance_summary(
    db: AsyncSession,
    params: PageParams,
    *,
    year: int | None,
    month: int | None,
    category_id: uuid.UUID | None,
    search: str | None,
) -> tuple[list[AttendanceSummaryItem], int]:
    present = func.count().filter(StudentAttendance.status == AttendanceStatus.PRESENT)
    absent = func.count().filter(StudentAttendance.status == AttendanceStatus.ABSENT)
    informed = func.count().filter(StudentAttendance.status == AttendanceStatus.INFORMED)
    stmt = (
        select(Student, func.count(), present, absent, informed)
        .join(StudentAttendance, StudentAttendance.student_id == Student.id)
        .where(*_period_filter(StudentAttendance.session_date, year, month, None))
        .group_by(Student.id)
        .order_by(Student.full_name)
    )
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if search:
        like = contains(search)
        stmt = stmt.where(or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(stmt.limit(params.page_size).offset(params.offset))).all()
    items = []
    for student, n, p, a, i in rows:
        rate = round(100 * p / n, 1) if n else None
        items.append(
            AttendanceSummaryItem(
                student=AttendanceStudent.model_validate(student),
                category=RefItem.model_validate(student.category),
                total_sessions=n,
                present=p,
                absent=a,
                informed=i,
                rate=rate,
                band=band(rate),
            )
        )
    return items, total


def coach_attendance_query(
    *, year: int | None, month: int | None, day: date | None, search: str | None
) -> Select[*tuple[Any, ...]]:
    stmt: Select[*tuple[Any, ...]] = (
        select(SessionCoach, TrainingSession)
        .join(TrainingSession, TrainingSession.id == SessionCoach.session_id)
        .join(CoachProfile, CoachProfile.id == SessionCoach.coach_id)
        .join(User, User.id == CoachProfile.user_id)
        .where(*_period_filter(SessionCoach.session_date, year, month, day))
    )
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(User.full_name.ilike(like), CoachProfile.phone.like(like), TrainingSession.venue.ilike(like))
        )
    return stmt.order_by(SessionCoach.session_date.desc(), User.full_name)


def coach_stream_item(sc: SessionCoach, session: TrainingSession) -> CoachAttendanceStreamItem:
    return CoachAttendanceStreamItem(
        date=sc.session_date,
        session_id=session.id,
        coach=coach_ref(sc.coach),
        phone=sc.coach.phone,
        venue=session.venue,
        categories=[c.name for c in session.categories],
        role=sc.role.value,
        status=sc.status,
        remarks=sc.remarks,
    )


async def coach_attendance_stream(
    db: AsyncSession, params: PageParams, **filters: Any
) -> tuple[list[CoachAttendanceStreamItem], int]:
    stmt = coach_attendance_query(**filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(stmt.limit(params.page_size).offset(params.offset))).all()
    return [coach_stream_item(sc, s) for sc, s in rows], total


async def student_attendance_history(
    db: AsyncSession, student_id: uuid.UUID, *, year: int | None, month: int | None
) -> list[tuple[StudentAttendance, TrainingSession]]:
    stmt = (
        select(StudentAttendance, TrainingSession)
        .join(TrainingSession, TrainingSession.id == StudentAttendance.session_id)
        .where(
            StudentAttendance.student_id == student_id,
            *_period_filter(StudentAttendance.session_date, year, month, None),
        )
        .order_by(StudentAttendance.session_date.desc())
        .limit(1000)
    )
    return [(a, s) for a, s in (await db.execute(stmt)).all()]
