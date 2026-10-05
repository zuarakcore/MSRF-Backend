"""Admin dashboard aggregates and global search (read-only)."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, require_admin
from app.core.enums import RecordStatus
from app.core.pagination import PageParams
from app.core.schemas import CamelModel, MoneyOut
from app.core.search import contains
from app.core.timeutils import month_start, period_bounds, today_local
from app.modules.coaches.models import CoachProfile
from app.modules.fees import service as fees
from app.modules.fees.models import FeeLedgerEntry, Payment, PaymentStatus
from app.modules.fees.schemas import LedgerRow
from app.modules.payment_submissions import service as submissions
from app.modules.payment_submissions.models import PaymentSubmission, SubmissionStatus
from app.modules.payment_submissions.schemas import SubmissionOut
from app.modules.reference.models import Category
from app.modules.students import service as students
from app.modules.students.models import Student
from app.modules.students.schemas import StudentListItem
from app.modules.users.models import User
from app.modules.website.models import (
    ApplicationStatus,
    Enquiry,
    EnquiryStatus,
    GalleryItem,
    Job,
    JobApplication,
    JobStatus,
    Programme,
    TeamMember,
)

router = APIRouter(tags=["Dashboard"], dependencies=[Depends(require_admin)])


class StudentCounts(CamelModel):
    total: int
    active: int


class FeeTotals(CamelModel):
    outstanding: MoneyOut
    collected_this_month: MoneyOut
    collected_this_year: MoneyOut
    overdue_students: int


class CmsCounts(CamelModel):
    categories: int
    programmes: int
    team: int
    gallery: int
    open_jobs: int
    applications_under_review: int
    new_enquiries: int


class DashboardSummary(CamelModel):
    students: StudentCounts
    active_coaches: int
    fees: FeeTotals
    pending_verifications: int
    cms: CmsCounts
    recent_admissions: list[StudentListItem]
    recent_submissions: list[SubmissionOut]
    top_overdue: list[LedgerRow]


async def _count(db: DbSession, *where: object, model: object) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0  # type: ignore[arg-type]


@router.get(
    "/dashboard/summary", response_model=DashboardSummary, summary="Everything the admin dashboard shows"
)
async def summary(db: DbSession) -> DashboardSummary:
    today = today_local()
    month_from, month_to = period_bounds(today.year, today.month)
    year_from, year_to = period_bounds(today.year)
    e = FeeLedgerEntry

    async def collected(start: object, end: object) -> Decimal:
        value = await db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.VALID, Payment.paid_on >= start, Payment.paid_on < end
            )
        )
        return Decimal(value or 0)

    outstanding = await db.scalar(
        select(func.coalesce(func.sum(func.greatest(e.amount_due - e.discount - e.amount_paid, 0)), 0)).where(
            e.period <= month_start(today)
        )
    )
    overdue_students = (
        await db.scalar(
            select(func.count(func.distinct(e.student_id))).where(
                e.amount_paid + e.discount < e.amount_due, e.due_date < today
            )
        )
        or 0
    )

    recent_students, _ = await students.list_students(db, PageParams(page=1, page_size=5), sort="-createdAt")
    recent_subs, _ = await submissions.list_submissions(
        db, PageParams(page=1, page_size=4), status=None, search=None
    )
    overdue_rows, _ = await fees.ledger_rows(
        db,
        PageParams(page=1, page_size=5),
        year=today.year,
        month=today.month,
        category_id=None,
        status="OVERDUE",
        search=None,
    )

    return DashboardSummary(
        students=StudentCounts(
            total=await _count(db, model=Student),
            active=await _count(db, Student.status == RecordStatus.ACTIVE, model=Student),
        ),
        active_coaches=await db.scalar(
            select(func.count())
            .select_from(CoachProfile)
            .join(User, User.id == CoachProfile.user_id)
            .where(User.is_active.is_(True))
        )
        or 0,
        fees=FeeTotals(
            outstanding=Decimal(outstanding or 0),
            collected_this_month=await collected(month_from, month_to),
            collected_this_year=await collected(year_from, year_to),
            overdue_students=overdue_students,
        ),
        pending_verifications=await _count(
            db, PaymentSubmission.status == SubmissionStatus.PENDING, model=PaymentSubmission
        ),
        cms=CmsCounts(
            categories=await _count(db, Category.status == RecordStatus.ACTIVE, model=Category),
            programmes=await _count(db, Programme.status == RecordStatus.ACTIVE, model=Programme),
            team=await _count(db, TeamMember.status == RecordStatus.ACTIVE, model=TeamMember),
            gallery=await _count(db, GalleryItem.status == RecordStatus.ACTIVE, model=GalleryItem),
            open_jobs=await _count(db, Job.status == JobStatus.OPEN, model=Job),
            applications_under_review=await _count(
                db, JobApplication.status == ApplicationStatus.UNDER_REVIEW, model=JobApplication
            ),
            new_enquiries=await _count(db, Enquiry.status == EnquiryStatus.NEW, model=Enquiry),
        ),
        recent_admissions=recent_students,
        recent_submissions=recent_subs,
        top_overdue=overdue_rows,
    )


class SearchHit(CamelModel):
    id: uuid.UUID
    title: str
    subtitle: str
    link: str


class SearchResults(CamelModel):
    students: list[SearchHit]
    coaches: list[SearchHit]
    payment_submissions: list[SearchHit]
    payments: list[SearchHit]


@router.get("/search", response_model=SearchResults, summary="Global search (top 5 per type)")
async def search(db: DbSession, q: Annotated[str, Query(min_length=2, max_length=100)]) -> SearchResults:
    like = contains(q)
    student_rows = await db.scalars(
        select(Student)
        .where(
            or_(
                Student.full_name.ilike(like),
                Student.student_code.ilike(like),
                Student.parent_name.ilike(like),
                Student.parent_phone.like(like),
            )
        )
        .order_by(Student.full_name)
        .limit(5)
    )
    coach_rows = await db.scalars(
        select(CoachProfile)
        .join(User, User.id == CoachProfile.user_id)
        .where(or_(User.full_name.ilike(like), User.email.ilike(like), CoachProfile.phone.like(like)))
        .order_by(User.full_name)
        .limit(5)
    )
    sub_rows = await db.scalars(
        select(PaymentSubmission)
        .where(
            or_(
                PaymentSubmission.student_name.ilike(like),
                PaymentSubmission.submission_number.ilike(like),
                PaymentSubmission.transaction_reference.ilike(like),
                PaymentSubmission.parent_mobile.like(like),
            )
        )
        .order_by(PaymentSubmission.created_at.desc())
        .limit(5)
    )
    pay_rows = await db.scalars(
        select(Payment)
        .join(Student, Student.id == Payment.student_id)
        .where(
            or_(
                Payment.receipt_number.ilike(like),
                Payment.reference.ilike(like),
                Student.full_name.ilike(like),
            )
        )
        .order_by(Payment.created_at.desc())
        .limit(5)
    )
    return SearchResults(
        students=[
            SearchHit(
                id=s.id,
                title=s.full_name,
                subtitle=f"{s.student_code} · {s.category.name}",
                link=f"/super-admin/students/{s.id}",
            )
            for s in student_rows
        ],
        coaches=[
            SearchHit(
                id=c.id, title=c.user.full_name, subtitle=c.user.email, link=f"/super-admin/coaches/{c.id}"
            )
            for c in coach_rows
        ],
        payment_submissions=[
            SearchHit(
                id=p.id,
                title=p.student_name,
                subtitle=f"{p.submission_number} · ₹{p.amount:,.0f} · {p.status.value}",
                link="/super-admin/payments",
            )
            for p in sub_rows
        ],
        payments=[
            SearchHit(
                id=p.id,
                title=p.student.full_name,
                subtitle=f"{p.receipt_number} · ₹{p.amount:,.0f}",
                link=f"/super-admin/students/{p.student_id}",
            )
            for p in pay_rows
        ],
    )
