"""Coach portal read endpoints. Everything is scoped to the signed-in coach's categories, and the
student view deliberately omits fees, addresses and documents (OPEN_QUESTIONS Q9)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentCoach, DbSession, require_coach
from app.core.enums import AttendanceStatus, Batch, BloodGroup, Gender, RecordStatus
from app.core.errors import NotFound
from app.core.pagination import Page, Pagination, build_page
from app.core.schemas import CamelModel
from app.core.search import contains
from app.core.timeutils import period_bounds, today_local
from app.modules.coaches.models import CoachProfile
from app.modules.coaches.service import category_ids_of
from app.modules.files.schemas import Photo
from app.modules.performance import service as performance
from app.modules.performance.models import PerformanceReport
from app.modules.performance.schemas import PerformanceReportSummary
from app.modules.reference.schemas import RefItem
from app.modules.sessions import service as sessions
from app.modules.sessions.models import SessionCoach, StudentAttendance, TrainingSession
from app.modules.sessions.schemas import SessionListItem
from app.modules.students.models import Student

router = APIRouter(prefix="/coach", tags=["Coach Portal"], dependencies=[Depends(require_coach)])


class CoachStudentItem(CamelModel):
    id: uuid.UUID
    student_code: str
    full_name: str
    photo: Photo
    date_of_birth: date
    gender: Gender
    category: RefItem
    batch: Batch
    status: RecordStatus
    attendance_percentage: float | None = None


class CoachStudentDetail(CoachStudentItem):
    blood_group: BloodGroup | None
    parent_name: str
    parent_phone: str
    emergency_name: str | None
    emergency_phone: str | None


class TodayStatus(CamelModel):
    has_session: bool
    session_id: uuid.UUID | None


class CoachDashboard(CamelModel):
    full_name: str
    categories: list[RefItem]
    student_count: int
    attendance_rate_this_month: float | None
    reports_count: int
    today: TodayStatus
    recent_sessions: list[SessionListItem]


async def _scoped_student(db: AsyncSession, coach: CoachProfile, student_id: uuid.UUID) -> Student:
    student = await db.get(Student, student_id)
    if student is None or student.category_id not in await category_ids_of(db, coach.id):
        raise NotFound("Student not found", code="STUDENT_NOT_FOUND")
    return student


async def _rates(db: AsyncSession, student_ids: list[uuid.UUID]) -> dict[uuid.UUID, float | None]:
    if not student_ids:
        return {}
    rows = await db.execute(
        select(
            StudentAttendance.student_id,
            func.count().filter(StudentAttendance.status == AttendanceStatus.PRESENT),
            func.count(),
        )
        .where(StudentAttendance.student_id.in_(student_ids))
        .group_by(StudentAttendance.student_id)
    )
    return {sid: round(100 * p / n, 1) if n else None for sid, p, n in rows.all()}


@router.get("/dashboard", response_model=CoachDashboard, summary="My dashboard")
async def dashboard(coach: CurrentCoach, db: DbSession) -> CoachDashboard:
    category_ids = await category_ids_of(db, coach.id)
    student_count = (
        await db.scalar(
            select(func.count()).where(
                Student.category_id.in_(category_ids), Student.status == RecordStatus.ACTIVE
            )
        )
        or 0
    )
    today = today_local()
    start, end = period_bounds(today.year, today.month)
    present, total = (
        await db.execute(
            select(func.count().filter(StudentAttendance.status == AttendanceStatus.PRESENT), func.count())
            .join(SessionCoach, SessionCoach.session_id == StudentAttendance.session_id)
            .where(
                SessionCoach.coach_id == coach.id,
                StudentAttendance.session_date >= start,
                StudentAttendance.session_date < end,
            )
        )
    ).one()
    reports = await db.scalar(select(func.count()).where(PerformanceReport.coach_id == coach.id)) or 0
    today_session = await db.scalar(
        select(SessionCoach.session_id).where(
            SessionCoach.coach_id == coach.id, SessionCoach.session_date == today
        )
    )
    recent = list(
        await db.scalars(
            select(TrainingSession)
            .where(
                TrainingSession.id.in_(
                    select(SessionCoach.session_id).where(SessionCoach.coach_id == coach.id)
                )
            )
            .order_by(TrainingSession.session_date.desc())
            .limit(5)
        )
    )
    return CoachDashboard(
        full_name=coach.user.full_name,
        categories=[RefItem.model_validate(c) for c in coach.categories],
        student_count=student_count,
        attendance_rate_this_month=round(100 * present / total, 1) if total else None,
        reports_count=reports,
        today=TodayStatus(has_session=today_session is not None, session_id=today_session),
        recent_sessions=await sessions.list_items(db, recent, coach),
    )


@router.get("/students", response_model=Page[CoachStudentItem], summary="Students in my categories")
async def my_students(
    coach: CurrentCoach,
    db: DbSession,
    pagination: Pagination,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[uuid.UUID | None, Query(alias="categoryId")] = None,
    status_filter: Annotated[RecordStatus | None, Query(alias="status")] = RecordStatus.ACTIVE,
) -> Page[CoachStudentItem]:
    allowed = await category_ids_of(db, coach.id)
    stmt: Any = select(Student).where(Student.category_id.in_(allowed)).order_by(Student.full_name)
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if status_filter:
        stmt = stmt.where(Student.status == status_filter)
    if search:
        like = contains(search)
        stmt = stmt.where(or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    students = list(await db.scalars(stmt.limit(pagination.page_size).offset(pagination.offset)))
    rates = await _rates(db, [s.id for s in students])
    items = [
        CoachStudentItem.model_validate(s).model_copy(update={"attendance_percentage": rates.get(s.id)})
        for s in students
    ]
    return build_page(items, total, pagination)


@router.get("/students/{student_id}", response_model=CoachStudentDetail, summary="One of my students")
async def my_student(student_id: uuid.UUID, coach: CurrentCoach, db: DbSession) -> CoachStudentDetail:
    student = await _scoped_student(db, coach, student_id)
    rates = await _rates(db, [student.id])
    return CoachStudentDetail.model_validate(student).model_copy(
        update={"attendance_percentage": rates.get(student.id)}
    )


@router.get(
    "/students/{student_id}/performance-reports",
    response_model=list[PerformanceReportSummary],
    summary="Reports for one of my students (all coaches)",
)
async def my_student_reports(
    student_id: uuid.UUID, coach: CurrentCoach, db: DbSession
) -> list[PerformanceReportSummary]:
    await _scoped_student(db, coach, student_id)
    return await performance.reports_for_student(db, student_id)
