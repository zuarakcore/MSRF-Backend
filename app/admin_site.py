"""Django-admin-style back office at /admin, built with SQLAdmin.

Django equivalent: admin.py + admin.site.register(Model, ModelAdmin). Each `ModelView` below plays the
role of a ModelAdmin (list columns, search, sort, editable fields).

Rules:
* Only active ADMIN users can sign in (same users table and password hashing as the API).
* Money, attendance, reports and audit data are READ-ONLY here: those changes carry business rules
  (receipt numbers, ledger maths, 7-day locks, emails) that only the API enforces.
* Creating students/coaches/gallery photos is done through the API/admin app (codes, invites, uploads).
* Secrets (password hashes, tokens) are never shown.
"""

import hashlib

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.errors import AppError
from app.core.rate_limit import client_ip, enforce
from app.core.security import verify_password
from app.modules.audit.models import AuditLog
from app.modules.coaches.models import CoachProfile
from app.modules.fees.models import FeeLedgerEntry, Payment, PaymentAllocation
from app.modules.files.models import StoredFile
from app.modules.notifications.models import Notification
from app.modules.payment_submissions.models import PaymentSubmission
from app.modules.performance.models import PerformanceReport
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.sessions.models import StudentAttendance, TrainingSession
from app.modules.students.models import Student
from app.modules.users.models import Role, User
from app.modules.website.models import Enquiry, GalleryItem, Job, JobApplication, Programme, TeamMember

SESSION_MAX_AGE = 8 * 60 * 60  # 8 hours


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


class _ReadOnly(ModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = True
    page_size = 50


# --- people ----------------------------------------------------------------------------------


class UserAdmin(ModelView, model=User):
    name, name_plural, icon, category = "User", "Users", "fa-solid fa-user-shield", "Accounts"
    column_list = [
        User.email,
        User.full_name,
        User.role,
        User.is_active,
        User.is_verified,
        User.last_login_at,
    ]
    column_searchable_list = [User.email, User.full_name]
    column_sortable_list = [User.email, User.full_name, User.role, User.last_login_at]
    column_details_exclude_list = [User.password_hash, User.token_version]
    form_columns = [User.full_name, User.is_active]  # accounts are created by the API (invites) or CLI
    can_create = False
    can_delete = False


class CoachAdmin(ModelView, model=CoachProfile):
    name, name_plural, icon, category = "Coach", "Coaches", "fa-solid fa-whistle", "Accounts"
    column_list = [
        CoachProfile.user,
        CoachProfile.phone,
        CoachProfile.experience_years,
        CoachProfile.joined_date,
        CoachProfile.categories,
    ]
    column_searchable_list = [CoachProfile.phone]
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


class StudentAdmin(ModelView, model=Student):
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
    column_sortable_list = [Student.student_code, Student.full_name, Student.admission_date, Student.status]
    column_default_sort = [(Student.full_name, False)]
    form_excluded_columns = [
        Student.student_code,
        Student.photo,
        Student.photo_file_id,
        Student.created_at,
        Student.updated_at,
    ]
    can_create = False  # creation generates codes and the first fee month: use the admin app / API
    can_delete = False
    page_size = 50


# --- reference data --------------------------------------------------------------------------


class _RefAdmin(ModelView):
    column_searchable_list = ["name"]
    column_sortable_list = ["name", "sort_order", "status"]
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


# --- coaching (read-only) ----------------------------------------------------------------------


class TrainingSessionAdmin(_ReadOnly, model=TrainingSession):
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
    ]  # loaded on demand only


class AttendanceAdmin(_ReadOnly, model=StudentAttendance):
    name, name_plural, icon, category = "Attendance", "Attendance", "fa-solid fa-clipboard-check", "Coaching"
    column_list = [
        StudentAttendance.session_date,
        StudentAttendance.student,
        StudentAttendance.status,
        StudentAttendance.remarks,
    ]
    column_sortable_list = [StudentAttendance.session_date, StudentAttendance.status]
    column_default_sort = [(StudentAttendance.session_date, True)]


class PerformanceReportAdmin(_ReadOnly, model=PerformanceReport):
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


class PaymentAdmin(_ReadOnly, model=Payment):
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


class LedgerAdmin(_ReadOnly, model=FeeLedgerEntry):
    name, name_plural, icon, category = "Fee ledger entry", "Fee ledger", "fa-solid fa-book", "Finance"
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


class AllocationAdmin(_ReadOnly, model=PaymentAllocation):
    name, name_plural, icon, category = (
        "Allocation",
        "Payment allocations",
        "fa-solid fa-code-branch",
        "Finance",
    )
    column_list = [PaymentAllocation.ledger_entry, PaymentAllocation.amount]


class SubmissionAdmin(_ReadOnly, model=PaymentSubmission):
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
    column_details_exclude_list = [PaymentSubmission.submitter_ip]


# --- website ---------------------------------------------------------------------------------


class ProgrammeAdmin(ModelView, model=Programme):
    name, name_plural, icon, category = "Programme", "Programmes", "fa-solid fa-graduation-cap", "Website"
    column_list = [
        Programme.title,
        Programme.age_group,
        Programme.slug,
        Programme.status,
        Programme.sort_order,
    ]
    column_searchable_list = [Programme.title]
    column_sortable_list = [Programme.title, Programme.sort_order]
    form_excluded_columns = [Programme.slug, Programme.created_at, Programme.updated_at]


class TeamMemberAdmin(ModelView, model=TeamMember):
    name, name_plural, icon, category = "Team member", "Team members", "fa-solid fa-user-tie", "Website"
    column_list = [TeamMember.name, TeamMember.designation, TeamMember.status, TeamMember.sort_order]
    column_searchable_list = [TeamMember.name, TeamMember.designation]
    form_excluded_columns = [
        TeamMember.photo,
        TeamMember.photo_file_id,
        TeamMember.created_at,
        TeamMember.updated_at,
    ]


class GalleryAdmin(ModelView, model=GalleryItem):
    name, name_plural, icon, category = "Gallery photo", "Gallery", "fa-solid fa-images", "Website"
    column_list = [GalleryItem.title, GalleryItem.category, GalleryItem.status, GalleryItem.sort_order]
    column_searchable_list = [GalleryItem.title, GalleryItem.category]
    form_columns = [
        GalleryItem.title,
        GalleryItem.caption,
        GalleryItem.category,
        GalleryItem.is_wide,
        GalleryItem.status,
        GalleryItem.sort_order,
    ]
    can_create = False  # photos are uploaded (validated, resized) through the API


class JobAdmin(ModelView, model=Job):
    name, name_plural, icon, category = "Job", "Jobs", "fa-solid fa-briefcase", "Website"
    column_list = [Job.title, Job.location, Job.posted_on, Job.closing_date, Job.status]
    column_searchable_list = [Job.title, Job.location]
    form_excluded_columns = [Job.created_at, Job.updated_at]


class JobApplicationAdmin(ModelView, model=JobApplication):
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
    column_details_exclude_list = [JobApplication.submitter_ip]
    form_columns = [JobApplication.status]
    can_create = False


class EnquiryAdmin(ModelView, model=Enquiry):
    name, name_plural, icon, category = "Enquiry", "Enquiries", "fa-solid fa-envelope-open-text", "Website"
    column_list = [Enquiry.created_at, Enquiry.parent_name, Enquiry.phone, Enquiry.subject, Enquiry.status]
    column_searchable_list = [Enquiry.parent_name, Enquiry.phone]
    column_default_sort = [(Enquiry.created_at, True)]
    column_details_exclude_list = [Enquiry.submitter_ip]
    form_columns = [Enquiry.status]
    can_create = False


# --- system (read-only) ----------------------------------------------------------------------


class NotificationAdmin(_ReadOnly, model=Notification):
    name, name_plural, icon, category = "Notification", "Notifications", "fa-solid fa-bell", "System"
    column_list = [Notification.created_at, Notification.type, Notification.title, Notification.read_at]
    column_default_sort = [(Notification.created_at, True)]


class AuditLogAdmin(_ReadOnly, model=AuditLog):
    name, name_plural, icon, category = "Audit log", "Audit log", "fa-solid fa-shield-halved", "System"
    column_list = [AuditLog.created_at, AuditLog.action, AuditLog.entity_type, AuditLog.entity_id]
    column_searchable_list = [AuditLog.action, AuditLog.entity_type]
    column_default_sort = [(AuditLog.created_at, True)]


class StoredFileAdmin(_ReadOnly, model=StoredFile):
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


VIEWS: list[type[ModelView]] = [
    StudentAdmin,
    CoachAdmin,
    CategoryAdmin,
    ProgramTypeAdmin,
    TrainingCenterAdmin,
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
    UserAdmin,
    NotificationAdmin,
    AuditLogAdmin,
    StoredFileAdmin,
]


def mount_admin(app: FastAPI) -> Admin:
    settings = get_settings()
    secret = hashlib.sha256(b"admin-panel:" + settings.JWT_SECRET_KEY.get_secret_value().encode()).hexdigest()
    admin = Admin(
        app,
        session_maker=get_sessionmaker(),
        base_url="/admin",
        title="MSRF Admin",
        logo_url="/static/email-logo.png",
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
