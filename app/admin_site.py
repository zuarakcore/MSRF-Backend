"""Django-admin-style back office at /admin, built with SQLAdmin.

Django equivalent: admin.py + admin.site.register(Model, ModelAdmin). Each `ModelView` below plays the
role of a ModelAdmin (list columns, search, filters, editable fields).

Rules:
* Only active ADMIN users can sign in (same users table and password hashing as the API).
* Money, attendance, reports and audit data are READ-ONLY here: those changes carry business rules
  (receipt numbers, ledger maths, 7-day locks, emails) that only the API enforces.
* Creating students/coaches/gallery photos is done through the API/admin app (codes, invites, uploads).
* Secrets (password hashes, tokens) are never shown.

Friendliness (applied to every view through `FriendlyView`):
* human column labels, coloured status badges, Indian-format rupees, readable dates in IST,
* related records shown by name (each model's __str__), filters on the columns staff narrow by,
* a home dashboard with live counts linking to the lists that need attention.
"""

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from markupsafe import Markup
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend, login_required
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter, ForeignKeyFilter, StaticValuesFilter
from sqladmin.formatters import BASE_FORMATTERS
from sqlalchemy import func, inspect, select
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.enums import AttendanceStatus, Batch, RecordStatus
from app.core.errors import AppError
from app.core.rate_limit import client_ip, enforce
from app.core.security import verify_password
from app.core.timeutils import month_start, period_bounds, today_local
from app.modules.audit.models import AuditLog
from app.modules.coaches.models import CoachProfile
from app.modules.fees.models import FeeLedgerEntry, Payment, PaymentAllocation, PaymentMode, PaymentStatus
from app.modules.files.models import StoredFile
from app.modules.notifications.models import Notification, NotificationType
from app.modules.payment_submissions.models import PaymentSubmission, SubmissionMethod, SubmissionStatus
from app.modules.performance.models import PerformanceReport
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.sessions.models import StudentAttendance, TrainingSession
from app.modules.students.models import Student
from app.modules.users.models import Role, User
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

SESSION_MAX_AGE = 8 * 60 * 60  # 8 hours
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "admin"

# --- display formatting ------------------------------------------------------------------------

_BADGE_CLASSES = {
    "bg-green-lt": {"ACTIVE", "PAID", "VERIFIED", "VALID", "OPEN", "PRESENT", "SHORTLISTED", "RESOLVED"},
    "bg-red-lt": {"INACTIVE", "REJECTED", "VOIDED", "CLOSED", "ABSENT"},
    "bg-yellow-lt": {"PENDING", "UNDER_REVIEW", "NEW", "INFORMED", "CONTACTED"},
}


def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _badge(value: StrEnum) -> Markup:
    css = next((c for c, values in _BADGE_CLASSES.items() if value.value in values), "bg-secondary-lt")
    return Markup('<span class="badge {}">{}</span>').format(css, _humanize(value.value))


def _rupees(value: Decimal) -> str:
    """Indian digit grouping: 1234567.5 -> ₹12,34,567.50 (paise only when present)."""
    negative, value = value < 0, abs(value)
    whole, paise = divmod(value.quantize(Decimal("0.01")), 1)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join([head[max(i - 2, 0) : i] for i in range(len(head), 0, -2)][::-1])
        digits = f"{head},{tail}"
    text = f"₹{digits}" + (f".{int(paise * 100):02d}" if paise else "")
    return f"-{text}" if negative else text


def _date(value: date) -> str:
    return value.strftime("%d %b %Y")


def _datetime(value: datetime) -> str:
    return value.astimezone(ZoneInfo(get_settings().TIMEZONE)).strftime("%d %b %Y, %I:%M %p")


FORMATTERS: dict[Any, Any] = {
    **BASE_FORMATTERS,
    StrEnum: _badge,
    Decimal: _rupees,
    date: _date,
    datetime: _datetime,
}

# Labels where "snake_case -> Sentence case" is not good enough.
_LABELS = {
    "full_name": "Name",
    "user": "Account",
    "created_by": "Logged by",
    "is_active": "Active",
    "is_verified": "Password set",
    "is_wide": "Wide photo",
    "amount_due": "Fee due",
    "amount_paid": "Paid",
    "created_at": "Created",
    "updated_at": "Last updated",
    "sort_order": "Display order",
    "student_code": "Student ID",
    "experience_years": "Experience (years)",
    "joined_date": "Joined",
    "session_date": "Date",
    "daily_topic": "Topic",
    "recorded_date": "Recorded",
    "report_period": "Period",
    "overall_rating": "Rating",
    "receipt_number": "Receipt no.",
    "submission_number": "Submission no.",
    "payment_method": "Method",
    "entity_type": "Record type",
    "entity_id": "Record ID",
    "original_filename": "File name",
    "content_type": "Type",
    "size_bytes": "Size (bytes)",
    "read_at": "Read",
    "last_login_at": "Last login",
    "ledger_entry": "Fee month",
}


def _choices(enum_cls: type[StrEnum]) -> list[tuple[str, str]]:
    return [(m.value, _humanize(m.value)) for m in enum_cls]


class FriendlyView(ModelView):
    """Base for every admin page: readable labels, formatted values, sensible paging."""

    column_type_formatters = FORMATTERS
    page_size = 25
    page_size_options = [25, 50, 100]
    can_export = True

    def __init__(self) -> None:
        super().__init__()
        keys: list[str] = list(inspect(self.model).attrs.keys())
        auto = {key: _LABELS.get(key, _humanize(key.removesuffix("_id"))) for key in keys}
        self._column_labels = {**auto, **self._column_labels}  # explicit column_labels still win
        self._column_labels_value_by_key = {v: k for k, v in self._column_labels.items()}


class ReadOnlyView(FriendlyView):
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 50


# --- authentication ----------------------------------------------------------------------------


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        try:
            await enforce("admin-panel-login", "5/minute", client_ip(request), email)
        except AppError:
            return False
        async with get_sessionmaker()() as db:
            user = await db.scalar(select(User).where(User.email == email))
            valid, _ = verify_password(password, user.password_hash if user else None)
            if not (valid and user and user.is_active and user.role is Role.ADMIN):
                return False
            request.session.update({"user_id": str(user.id), "ver": user.token_version})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Re-checked on every request: deactivation or a password change ends the session at once."""
        user_id, version = request.session.get("user_id"), request.session.get("ver")
        if not user_id:
            return False
        async with get_sessionmaker()() as db:
            user = await db.get(User, user_id)
        ok = bool(user and user.is_active and user.role is Role.ADMIN and user.token_version == version)
        if not ok:
            request.session.clear()
        return ok


# --- academy -----------------------------------------------------------------------------------


class StudentAdmin(FriendlyView, model=Student):
    name, name_plural, icon, category = "Student", "Students", "fa-solid fa-children", "Academy"
    column_list = [
        Student.student_code,
        Student.full_name,
        Student.category,
        Student.batch,
        Student.parent_name,
        Student.parent_phone,
        Student.monthly_fee,
        Student.status,
    ]
    column_searchable_list = [
        Student.full_name,
        Student.student_code,
        Student.parent_name,
        Student.parent_phone,
    ]
    column_sortable_list = [
        Student.student_code,
        Student.full_name,
        Student.admission_date,
        Student.monthly_fee,
        Student.status,
    ]
    column_default_sort = [(Student.full_name, False)]
    column_filters = [
        StaticValuesFilter(Student.status, _choices(RecordStatus), title="Status"),
        ForeignKeyFilter(Student.category_id, Category.name, foreign_model=Category, title="Category"),
        ForeignKeyFilter(
            Student.training_center_id,
            TrainingCenter.name,
            foreign_model=TrainingCenter,
            title="Training center",
        ),
        StaticValuesFilter(Student.batch, _choices(Batch), title="Batch"),
    ]
    column_details_exclude_list = [Student.photo_file_id, Student.photo]
    form_excluded_columns = [
        Student.student_code,
        Student.photo,
        Student.photo_file_id,
        Student.created_at,
        Student.updated_at,
    ]
    can_create = False  # creation generates codes and the first fee month: use the admin app / API
    can_delete = False


class _RefAdmin(FriendlyView):
    column_searchable_list = ["name"]
    column_sortable_list = ["name", "sort_order", "status"]
    column_default_sort = [("sort_order", False), ("name", False)]
    column_filters = [StaticValuesFilter("status", _choices(RecordStatus), title="Status")]
    form_excluded_columns = ["created_at", "updated_at"]
    category = "Academy"


class CategoryAdmin(_RefAdmin, model=Category):
    name, name_plural, icon = "Category", "Categories", "fa-solid fa-layer-group"
    column_list = [Category.name, Category.description, Category.status, Category.sort_order]


class ProgramTypeAdmin(_RefAdmin, model=ProgramType):
    name, name_plural, icon = "Program type", "Program types", "fa-solid fa-list"
    column_list = [ProgramType.name, ProgramType.description, ProgramType.status, ProgramType.sort_order]


class TrainingCenterAdmin(_RefAdmin, model=TrainingCenter):
    name, name_plural, icon = "Training center", "Training centers", "fa-solid fa-location-dot"
    column_list = [TrainingCenter.name, TrainingCenter.location, TrainingCenter.phone, TrainingCenter.status]


# --- accounts ----------------------------------------------------------------------------------


class CoachAdmin(FriendlyView, model=CoachProfile):
    name, name_plural, icon, category = "Coach", "Coaches", "fa-solid fa-person-chalkboard", "Accounts"
    column_list = [
        CoachProfile.user,
        CoachProfile.phone,
        CoachProfile.experience_years,
        CoachProfile.joined_date,
        CoachProfile.categories,
    ]
    column_searchable_list = [CoachProfile.phone]
    column_sortable_list = [CoachProfile.experience_years, CoachProfile.joined_date]
    column_details_exclude_list = [CoachProfile.photo_file_id, CoachProfile.photo, CoachProfile.user_id]
    form_columns = [
        CoachProfile.phone,
        CoachProfile.gender,
        CoachProfile.blood_group,
        CoachProfile.experience_years,
        CoachProfile.joined_date,
        CoachProfile.address,
        CoachProfile.bio,
        CoachProfile.categories,
    ]
    can_create = False  # creation sends the credentials email: use the admin app / API
    can_delete = False


class UserAdmin(FriendlyView, model=User):
    name, name_plural, icon, category = "User", "Users", "fa-solid fa-user-shield", "Accounts"
    column_list = [
        User.full_name,
        User.email,
        User.role,
        User.is_active,
        User.is_verified,
        User.last_login_at,
    ]
    column_searchable_list = [User.email, User.full_name]
    column_sortable_list = [User.email, User.full_name, User.role, User.last_login_at]
    column_default_sort = [(User.full_name, False)]
    column_filters = [
        StaticValuesFilter(User.role, _choices(Role), title="Role"),
        BooleanFilter(User.is_active, title="Active"),
    ]
    column_details_exclude_list = [User.password_hash, User.token_version]
    form_columns = [User.full_name, User.is_active]  # accounts are created by the API (invites) or CLI
    can_create = False
    can_delete = False


# --- coaching (read-only) ----------------------------------------------------------------------


class TrainingSessionAdmin(ReadOnlyView, model=TrainingSession):
    name, name_plural, icon, category = (
        "Training session",
        "Training sessions",
        "fa-solid fa-futbol",
        "Coaching",
    )
    column_list = [
        TrainingSession.session_date,
        TrainingSession.created_by,
        TrainingSession.venue,
        TrainingSession.daily_topic,
        TrainingSession.categories,
    ]
    column_sortable_list = [TrainingSession.session_date]
    column_default_sort = [(TrainingSession.session_date, True)]
    column_searchable_list = [TrainingSession.daily_topic, TrainingSession.venue]
    column_details_exclude_list = [
        TrainingSession.splits,
        TrainingSession.attendance,
        TrainingSession.created_by_coach_id,
    ]  # attendance is large: open it from the Attendance list instead


class AttendanceAdmin(ReadOnlyView, model=StudentAttendance):
    name, name_plural, icon, category = "Attendance", "Attendance", "fa-solid fa-clipboard-check", "Coaching"
    column_list = [
        StudentAttendance.session_date,
        StudentAttendance.student,
        StudentAttendance.status,
        StudentAttendance.remarks,
    ]
    column_sortable_list = [StudentAttendance.session_date, StudentAttendance.status]
    column_default_sort = [(StudentAttendance.session_date, True)]
    column_filters = [
        StaticValuesFilter(StudentAttendance.status, _choices(AttendanceStatus), title="Status")
    ]


class PerformanceReportAdmin(ReadOnlyView, model=PerformanceReport):
    name, name_plural, icon, category = (
        "Performance report",
        "Performance reports",
        "fa-solid fa-star",
        "Coaching",
    )
    column_list = [
        PerformanceReport.recorded_date,
        PerformanceReport.student,
        PerformanceReport.coach,
        PerformanceReport.report_period,
        PerformanceReport.overall_rating,
    ]
    column_sortable_list = [PerformanceReport.recorded_date, PerformanceReport.overall_rating]
    column_default_sort = [(PerformanceReport.recorded_date, True)]


# --- finance (read-only) -----------------------------------------------------------------------


class PaymentAdmin(ReadOnlyView, model=Payment):
    name, name_plural, icon, category = "Payment", "Payments", "fa-solid fa-receipt", "Finance"
    column_list = [
        Payment.receipt_number,
        Payment.paid_on,
        Payment.student,
        Payment.amount,
        Payment.mode,
        Payment.source,
        Payment.status,
    ]
    column_searchable_list = [Payment.receipt_number, Payment.reference]
    column_sortable_list = [Payment.paid_on, Payment.amount, Payment.receipt_number]
    column_default_sort = [(Payment.paid_on, True)]
    column_filters = [
        StaticValuesFilter(Payment.mode, _choices(PaymentMode), title="Mode"),
        StaticValuesFilter(Payment.status, _choices(PaymentStatus), title="Status"),
    ]


class LedgerAdmin(ReadOnlyView, model=FeeLedgerEntry):
    name, name_plural, icon, category = "Fee month", "Fee ledger", "fa-solid fa-book", "Finance"
    column_list = [
        FeeLedgerEntry.period,
        FeeLedgerEntry.student,
        FeeLedgerEntry.amount_due,
        FeeLedgerEntry.discount,
        FeeLedgerEntry.amount_paid,
        FeeLedgerEntry.due_date,
    ]
    column_sortable_list = [FeeLedgerEntry.period, FeeLedgerEntry.amount_due, FeeLedgerEntry.amount_paid]
    column_default_sort = [(FeeLedgerEntry.period, True)]
    column_labels = {FeeLedgerEntry.period: "Month"}


class AllocationAdmin(ReadOnlyView, model=PaymentAllocation):
    name, name_plural, icon, category = (
        "Allocation",
        "Payment allocations",
        "fa-solid fa-code-branch",
        "Finance",
    )
    column_list = [PaymentAllocation.ledger_entry, PaymentAllocation.amount]


class SubmissionAdmin(ReadOnlyView, model=PaymentSubmission):
    name, name_plural, icon, category = (
        "Payment submission",
        "Payment submissions",
        "fa-solid fa-inbox",
        "Finance",
    )
    column_list = [
        PaymentSubmission.submission_number,
        PaymentSubmission.created_at,
        PaymentSubmission.student_name,
        PaymentSubmission.parent_mobile,
        PaymentSubmission.amount,
        PaymentSubmission.payment_method,
        PaymentSubmission.status,
    ]
    column_searchable_list = [
        PaymentSubmission.student_name,
        PaymentSubmission.submission_number,
        PaymentSubmission.parent_mobile,
    ]
    column_sortable_list = [PaymentSubmission.created_at, PaymentSubmission.amount]
    column_default_sort = [(PaymentSubmission.created_at, True)]
    column_filters = [
        StaticValuesFilter(PaymentSubmission.status, _choices(SubmissionStatus), title="Status"),
        StaticValuesFilter(PaymentSubmission.payment_method, _choices(SubmissionMethod), title="Method"),
    ]
    column_details_exclude_list = [PaymentSubmission.submitter_ip, PaymentSubmission.screenshot_file_id]
    column_labels = {PaymentSubmission.created_at: "Submitted"}


# --- website -----------------------------------------------------------------------------------


class ProgrammeAdmin(FriendlyView, model=Programme):
    name, name_plural, icon, category = "Programme", "Programmes", "fa-solid fa-graduation-cap", "Website"
    column_list = [Programme.title, Programme.age_group, Programme.status, Programme.sort_order]
    column_searchable_list = [Programme.title]
    column_sortable_list = [Programme.title, Programme.sort_order]
    column_default_sort = [(Programme.sort_order, False)]
    column_filters = [StaticValuesFilter(Programme.status, _choices(RecordStatus), title="Status")]
    form_excluded_columns = [Programme.slug, Programme.created_at, Programme.updated_at]


class TeamMemberAdmin(FriendlyView, model=TeamMember):
    name, name_plural, icon, category = "Team member", "Team members", "fa-solid fa-user-tie", "Website"
    column_list = [TeamMember.name, TeamMember.designation, TeamMember.status, TeamMember.sort_order]
    column_searchable_list = [TeamMember.name, TeamMember.designation]
    column_default_sort = [(TeamMember.sort_order, False)]
    column_details_exclude_list = [TeamMember.photo_file_id, TeamMember.photo]
    form_excluded_columns = [
        TeamMember.photo,
        TeamMember.photo_file_id,
        TeamMember.created_at,
        TeamMember.updated_at,
    ]


class GalleryAdmin(FriendlyView, model=GalleryItem):
    name, name_plural, icon, category = "Gallery photo", "Gallery", "fa-solid fa-images", "Website"
    column_list = [GalleryItem.title, GalleryItem.category, GalleryItem.status, GalleryItem.sort_order]
    column_searchable_list = [GalleryItem.title, GalleryItem.category]
    column_default_sort = [(GalleryItem.sort_order, False)]
    column_filters = [
        AllUniqueStringValuesFilter(GalleryItem.category, title="Category"),
        StaticValuesFilter(GalleryItem.status, _choices(RecordStatus), title="Status"),
    ]
    column_details_exclude_list = [GalleryItem.image_file_id, GalleryItem.thumbnail_file_id]
    form_columns = [
        GalleryItem.title,
        GalleryItem.caption,
        GalleryItem.category,
        GalleryItem.is_wide,
        GalleryItem.status,
        GalleryItem.sort_order,
    ]
    can_create = False  # photos are uploaded (validated, resized) through the API


class JobAdmin(FriendlyView, model=Job):
    name, name_plural, icon, category = "Job", "Jobs", "fa-solid fa-briefcase", "Website"
    column_list = [Job.title, Job.location, Job.posted_on, Job.closing_date, Job.status]
    column_searchable_list = [Job.title, Job.location]
    column_default_sort = [(Job.posted_on, True)]
    column_filters = [StaticValuesFilter(Job.status, _choices(JobStatus), title="Status")]
    form_excluded_columns = [Job.created_at, Job.updated_at]


class JobApplicationAdmin(FriendlyView, model=JobApplication):
    name, name_plural, icon, category = (
        "Job application",
        "Job applications",
        "fa-solid fa-file-lines",
        "Website",
    )
    column_list = [
        JobApplication.created_at,
        JobApplication.full_name,
        JobApplication.job,
        JobApplication.phone,
        JobApplication.status,
    ]
    column_searchable_list = [JobApplication.full_name, JobApplication.phone]
    column_default_sort = [(JobApplication.created_at, True)]
    column_filters = [StaticValuesFilter(JobApplication.status, _choices(ApplicationStatus), title="Status")]
    column_details_exclude_list = [
        JobApplication.submitter_ip,
        JobApplication.cv_file_id,
        JobApplication.job_id,
    ]
    column_labels = {JobApplication.created_at: "Applied", JobApplication.job: "Position"}
    form_columns = [JobApplication.status]
    can_create = False


class EnquiryAdmin(FriendlyView, model=Enquiry):
    name, name_plural, icon, category = "Enquiry", "Enquiries", "fa-solid fa-envelope-open-text", "Website"
    column_list = [Enquiry.created_at, Enquiry.parent_name, Enquiry.phone, Enquiry.subject, Enquiry.status]
    column_searchable_list = [Enquiry.parent_name, Enquiry.phone]
    column_default_sort = [(Enquiry.created_at, True)]
    column_filters = [StaticValuesFilter(Enquiry.status, _choices(EnquiryStatus), title="Status")]
    column_details_exclude_list = [Enquiry.submitter_ip, Enquiry.programme_id]
    column_labels = {Enquiry.created_at: "Received", Enquiry.subject: "Programme"}
    form_columns = [Enquiry.status]
    can_create = False


# --- system (read-only) ------------------------------------------------------------------------


class NotificationAdmin(ReadOnlyView, model=Notification):
    name, name_plural, icon, category = "Notification", "Notifications", "fa-solid fa-bell", "System"
    column_list = [Notification.created_at, Notification.type, Notification.title, Notification.read_at]
    column_default_sort = [(Notification.created_at, True)]
    column_filters = [StaticValuesFilter(Notification.type, _choices(NotificationType), title="Type")]


class AuditLogAdmin(ReadOnlyView, model=AuditLog):
    name, name_plural, icon, category = "Audit log", "Audit log", "fa-solid fa-shield-halved", "System"
    column_list = [AuditLog.created_at, AuditLog.action, AuditLog.entity_type, AuditLog.entity_id]
    column_searchable_list = [AuditLog.action, AuditLog.entity_type]
    column_default_sort = [(AuditLog.created_at, True)]
    column_filters = [AllUniqueStringValuesFilter(AuditLog.action, title="Action")]
    column_labels = {AuditLog.created_at: "When"}


class StoredFileAdmin(ReadOnlyView, model=StoredFile):
    name, name_plural, icon, category = "Stored file", "Stored files", "fa-solid fa-folder-open", "System"
    column_list = [
        StoredFile.created_at,
        StoredFile.purpose,
        StoredFile.original_filename,
        StoredFile.content_type,
        StoredFile.size_bytes,
        StoredFile.visibility,
    ]
    column_default_sort = [(StoredFile.created_at, True)]
    column_details_exclude_list = [StoredFile.storage_key, StoredFile.sha256]
    column_labels = {StoredFile.created_at: "Uploaded"}


VIEWS: list[type[ModelView]] = [
    StudentAdmin,
    CategoryAdmin,
    ProgramTypeAdmin,
    TrainingCenterAdmin,
    CoachAdmin,
    UserAdmin,
    TrainingSessionAdmin,
    AttendanceAdmin,
    PerformanceReportAdmin,
    PaymentAdmin,
    LedgerAdmin,
    AllocationAdmin,
    SubmissionAdmin,
    ProgrammeAdmin,
    TeamMemberAdmin,
    GalleryAdmin,
    JobAdmin,
    JobApplicationAdmin,
    EnquiryAdmin,
    NotificationAdmin,
    AuditLogAdmin,
    StoredFileAdmin,
]


# --- home dashboard ----------------------------------------------------------------------------


def _count(model: Any, *where: Any) -> Any:
    return select(func.count()).select_from(model).where(*where).scalar_subquery()


class MSRFAdmin(Admin):
    """The /admin home page: today's numbers, each card linking to the list behind it."""

    @login_required
    async def index(self, request: Request) -> Response:
        today = today_local()
        month_from, month_to = period_bounds(today.year, today.month)
        e = FeeLedgerEntry
        unpaid = e.amount_paid + e.discount < e.amount_due
        async with get_sessionmaker()() as db:
            row = (
                await db.execute(
                    select(
                        _count(Student, Student.status == RecordStatus.ACTIVE),
                        _count(CoachProfile.__table__.join(User.__table__), User.is_active.is_(True)),
                        _count(PaymentSubmission, PaymentSubmission.status == SubmissionStatus.PENDING),
                        select(func.count(func.distinct(e.student_id)))
                        .where(unpaid, e.due_date < today)
                        .scalar_subquery(),
                        select(func.coalesce(func.sum(Payment.amount), 0))
                        .where(
                            Payment.status == PaymentStatus.VALID,
                            Payment.paid_on >= month_from,
                            Payment.paid_on < month_to,
                        )
                        .scalar_subquery(),
                        select(func.coalesce(func.sum(e.amount_due - e.discount - e.amount_paid), 0))
                        .where(unpaid, e.period <= month_start(today))
                        .scalar_subquery(),
                        _count(Enquiry, Enquiry.status == EnquiryStatus.NEW),
                        _count(JobApplication, JobApplication.status == ApplicationStatus.UNDER_REVIEW),
                        _count(TrainingSession, TrainingSession.session_date > today - timedelta(days=7)),
                    )
                )
            ).one()
            payments = list(await db.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(6)))
            enquiries = list(await db.scalars(select(Enquiry).order_by(Enquiry.created_at.desc()).limit(6)))

        students, coaches, to_verify, overdue, collected, outstanding, new_enquiries, to_review, sessions = (
            row
        )

        def url(view: type[ModelView]) -> str:
            return str(request.url_for("admin:list", identity=view.identity))

        def attention(n: Any) -> str:
            return "yellow" if n else "green"

        cards = [
            ("Active students", students, url(StudentAdmin), "green", "fa-children"),
            ("Active coaches", coaches, url(CoachAdmin), "green", "fa-person-chalkboard"),
            ("Payments to verify", to_verify, url(SubmissionAdmin), attention(to_verify), "fa-inbox"),
            (
                "Students with overdue fees",
                overdue,
                url(LedgerAdmin),
                "red" if overdue else "green",
                "fa-triangle-exclamation",
            ),
            (
                "Collected this month",
                _rupees(Decimal(collected)),
                url(PaymentAdmin),
                "green",
                "fa-indian-rupee-sign",
            ),
            (
                "Fees outstanding",
                _rupees(Decimal(outstanding)),
                url(LedgerAdmin),
                attention(outstanding),
                "fa-scale-unbalanced",
            ),
            ("New enquiries", new_enquiries, url(EnquiryAdmin), attention(new_enquiries), "fa-envelope"),
            (
                "Applications to review",
                to_review,
                url(JobApplicationAdmin),
                attention(to_review),
                "fa-file-lines",
            ),
            ("Sessions in the last 7 days", sessions, url(TrainingSessionAdmin), "blue", "fa-futbol"),
        ]
        return await self.templates.TemplateResponse(
            request,
            "msrf_overview.html",
            {
                "subtitle": today.strftime("%A, %d %B %Y"),
                "cards": cards,
                "payments": [
                    (p.receipt_number, str(p.student), _rupees(p.amount), _date(p.paid_on), _badge(p.status))
                    for p in payments
                ],
                "enquiries": [
                    (_datetime(q.created_at), q.parent_name, q.subject or "—", _badge(q.status))
                    for q in enquiries
                ],
                "payments_url": url(PaymentAdmin),
                "enquiries_url": url(EnquiryAdmin),
            },
        )


def mount_admin(app: FastAPI) -> Admin:
    settings = get_settings()
    secret = hashlib.sha256(b"admin-panel:" + settings.JWT_SECRET_KEY.get_secret_value().encode()).hexdigest()
    admin = MSRFAdmin(
        app,
        session_maker=get_sessionmaker(),
        base_url="/admin",
        title="MSRF Admin",
        logo_url="/static/email-logo.png",
        favicon_url="/static/email-logo.png",
        templates_dir=str(TEMPLATES_DIR),
        authentication_backend=AdminAuth(
            secret_key=secret,
            session_cookie="msrf_admin",
            max_age=SESSION_MAX_AGE,
            same_site="strict",
            https_only=settings.is_production,
        ),
    )
    for view in VIEWS:
        admin.add_view(view)
    return admin
