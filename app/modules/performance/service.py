import uuid
from datetime import date
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import INTEGRITY_ERROR_MAP, BusinessRuleViolation, Conflict, Forbidden, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import today_local
from app.modules.coaches.models import CoachProfile
from app.modules.coaches.service import category_ids_of
from app.modules.performance.models import (
    POSITIONS,
    SKILL_LABELS,
    STANDARD_GOALS,
    PerformanceReport,
    PerformanceSkillRating,
)
from app.modules.performance.schemas import (
    PerformanceConfig,
    PerformanceReportDetail,
    PerformanceReportIn,
    PerformanceReportSummary,
    ReportStudent,
    SkillLabel,
    SkillOut,
)
from app.modules.reference.schemas import RefItem
from app.modules.sessions.service import EDIT_WINDOW_DAYS, coach_ref, within_edit_window
from app.modules.students.models import Student

INTEGRITY_ERROR_MAP["uq_performance_reports_student_period"] = (
    409,
    "REPORT_EXISTS_FOR_PERIOD",
    "A report for this student and period already exists",
)


def config() -> PerformanceConfig:
    return PerformanceConfig(
        skills=[SkillLabel(key=k, label=v) for k, v in SKILL_LABELS.items()],
        standard_goals=list(STANDARD_GOALS),
        positions=list(POSITIONS),
    )


def _age(dob: date) -> int:
    today = today_local()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _can_edit(report: PerformanceReport, viewer: CoachProfile | None) -> bool:
    return viewer is not None and report.coach_id == viewer.id and within_edit_window(report.recorded_date)


def summary(report: PerformanceReport, viewer: CoachProfile | None = None) -> PerformanceReportSummary:
    return PerformanceReportSummary(
        id=report.id,
        student=RefItem(id=report.student.id, name=report.student.full_name),
        coach=coach_ref(report.coach),
        report_period=report.report_period,
        recorded_date=report.recorded_date,
        position=report.position,
        overall_rating=report.overall_rating,
        can_edit=_can_edit(report, viewer),
    )


def detail(report: PerformanceReport, viewer: CoachProfile | None = None) -> PerformanceReportDetail:
    s = report.student
    return PerformanceReportDetail(
        id=report.id,
        student=ReportStudent(
            id=s.id,
            student_code=s.student_code,
            full_name=s.full_name,
            date_of_birth=s.date_of_birth,
            age=_age(s.date_of_birth),
        ),
        coach=coach_ref(report.coach),
        report_period=report.report_period,
        recorded_date=report.recorded_date,
        position=report.position,
        strong_foot=report.strong_foot,
        skills=[
            SkillOut(skill=r.skill, label=SKILL_LABELS[r.skill], rating=r.rating, comment=r.comment)
            for r in report.skills
        ],
        strengths=report.strengths,
        areas_for_improvement=report.areas_for_improvement,
        development_goals=list(report.development_goals),
        custom_goal=report.custom_goal,
        coach_remarks=report.coach_remarks,
        overall_rating=report.overall_rating,
        can_edit=_can_edit(report, viewer),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


async def _student_in_scope(db: AsyncSession, coach: CoachProfile, student_id: uuid.UUID) -> Student:
    allowed = await category_ids_of(db, coach.id)
    student = await db.get(Student, student_id)
    if student is None or student.category_id not in allowed:
        raise NotFound("Student not found", code="STUDENT_NOT_FOUND")  # no existence leak across scopes
    return student


def _check_date(day: date) -> None:
    if not within_edit_window(day):
        raise BusinessRuleViolation(
            f"Recorded date must be between {EDIT_WINDOW_DAYS} days ago and today", code="DATE_OUT_OF_RANGE"
        )


async def _ensure_unique_period(
    db: AsyncSession, student_id: uuid.UUID, period: str, exclude: uuid.UUID | None = None
) -> None:
    stmt = select(PerformanceReport.id).where(
        PerformanceReport.student_id == student_id, PerformanceReport.report_period == period
    )
    if exclude:
        stmt = stmt.where(PerformanceReport.id != exclude)
    if await db.scalar(stmt):
        raise Conflict("A report for this student and period already exists", code="REPORT_EXISTS_FOR_PERIOD")


def _apply(report: PerformanceReport, data: PerformanceReportIn) -> None:
    for field in (
        "report_period",
        "recorded_date",
        "position",
        "strong_foot",
        "strengths",
        "areas_for_improvement",
        "development_goals",
        "custom_goal",
        "coach_remarks",
        "overall_rating",
    ):
        setattr(report, field, getattr(data, field))
    report.skills = [
        PerformanceSkillRating(skill=s.skill, rating=s.rating, comment=s.comment) for s in data.skills
    ]


async def _reload(db: AsyncSession, report_id: uuid.UUID) -> PerformanceReport:
    report = await db.scalar(
        select(PerformanceReport)
        .where(PerformanceReport.id == report_id)
        .execution_options(populate_existing=True)
    )
    assert report is not None
    return report


async def create_report(
    db: AsyncSession, coach: CoachProfile, data: PerformanceReportIn
) -> PerformanceReportDetail:
    await _student_in_scope(db, coach, data.student_id)
    _check_date(data.recorded_date)
    await _ensure_unique_period(db, data.student_id, data.report_period)
    report = PerformanceReport(student_id=data.student_id, coach_id=coach.id)
    report.skills = []
    _apply(report, data)
    db.add(report)
    await db.commit()
    return detail(await _reload(db, report.id), coach)


async def get_for_coach(db: AsyncSession, coach: CoachProfile, report_id: uuid.UUID) -> PerformanceReport:
    report = await db.get(PerformanceReport, report_id)
    if report is None or report.coach_id != coach.id:
        raise NotFound("Report not found", code="REPORT_NOT_FOUND")
    return report


def _require_editable(report: PerformanceReport) -> None:
    if not within_edit_window(report.recorded_date):
        raise Forbidden(f"Reports older than {EDIT_WINDOW_DAYS} days are locked", code="REPORT_LOCKED")


async def update_report(
    db: AsyncSession, coach: CoachProfile, report_id: uuid.UUID, data: PerformanceReportIn
) -> PerformanceReportDetail:
    report = await get_for_coach(db, coach, report_id)
    _require_editable(report)
    await _student_in_scope(db, coach, data.student_id)
    _check_date(data.recorded_date)
    await _ensure_unique_period(db, data.student_id, data.report_period, exclude=report_id)
    report.student_id = data.student_id
    # Ratings share a primary key (report_id, skill): remove the old rows before adding new ones.
    report.skills = []
    await db.flush()
    _apply(report, data)
    await db.commit()
    return detail(await _reload(db, report_id), coach)


async def delete_report(db: AsyncSession, coach: CoachProfile, report_id: uuid.UUID) -> None:
    report = await get_for_coach(db, coach, report_id)
    _require_editable(report)
    await db.delete(report)
    await db.commit()


def _filtered(
    stmt: Select[Any],
    *,
    search: str | None = None,
    student_id: uuid.UUID | None = None,
    coach_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select[Any]:
    if search:
        like = contains(search)
        stmt = stmt.join(Student, Student.id == PerformanceReport.student_id).where(
            or_(
                Student.full_name.ilike(like),
                PerformanceReport.position.ilike(like),
                PerformanceReport.report_period.ilike(like),
            )
        )
    if student_id:
        stmt = stmt.where(PerformanceReport.student_id == student_id)
    if coach_id:
        stmt = stmt.where(PerformanceReport.coach_id == coach_id)
    if date_from:
        stmt = stmt.where(PerformanceReport.recorded_date >= date_from)
    if date_to:
        stmt = stmt.where(PerformanceReport.recorded_date <= date_to)
    return stmt.order_by(PerformanceReport.recorded_date.desc(), PerformanceReport.created_at.desc())


async def list_reports(
    db: AsyncSession, params: PageParams, viewer: CoachProfile | None = None, **filters: Any
) -> tuple[list[PerformanceReportSummary], int]:
    stmt = _filtered(select(PerformanceReport), **filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    reports = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [summary(r, viewer) for r in reports], total


async def reports_for_student(db: AsyncSession, student_id: uuid.UUID) -> list[PerformanceReportSummary]:
    reports = await db.scalars(
        select(PerformanceReport)
        .where(PerformanceReport.student_id == student_id)
        .order_by(PerformanceReport.recorded_date.desc())
    )
    return [summary(r) for r in reports]


async def get_report(db: AsyncSession, report_id: uuid.UUID) -> PerformanceReport:
    report = await db.get(PerformanceReport, report_id)
    if report is None:
        raise NotFound("Report not found", code="REPORT_NOT_FOUND")
    return report
