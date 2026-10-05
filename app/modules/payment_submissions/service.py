import uuid
from decimal import Decimal

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.counters import next_code
from app.core.errors import BusinessRuleViolation, Conflict, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import today_local, utcnow
from app.modules.audit import service as audit
from app.modules.fees import service as fees
from app.modules.fees.models import Payment, PaymentMode, PaymentSource
from app.modules.files import service as files
from app.modules.files.models import FilePurpose
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationType
from app.modules.payment_submissions.models import PaymentSubmission, SubmissionMethod, SubmissionStatus
from app.modules.payment_submissions.schemas import (
    CandidateStudent,
    SubmissionForm,
    SubmissionOut,
    SubmissionPatch,
    VerifyIn,
)
from app.modules.reference.schemas import RefItem
from app.modules.students.models import Student
from app.modules.users.models import User


async def submit(
    db: AsyncSession, form: SubmissionForm, screenshot: UploadFile | None, ip: str | None
) -> PaymentSubmission:
    stored = None
    if screenshot is not None and screenshot.filename:
        stored = await files.store_upload(
            db, screenshot, purpose=FilePurpose.PAYMENT_SCREENSHOT, uploaded_by_id=None
        )
    if form.payment_method is SubmissionMethod.UPI and stored is None:
        raise BusinessRuleViolation("Upload your payment screenshot", code="SCREENSHOT_REQUIRED")

    submission = PaymentSubmission(
        submission_number=await next_code(db, "submission", today_local().year, "SUB-{year}-{n:05d}"),
        student_name=form.student_name,
        parent_mobile=form.parent_mobile,
        amount=form.amount,
        payment_date=form.payment_date,
        payment_method=form.payment_method,
        transaction_reference=form.transaction_reference,
        handed_over_to=form.handed_over_to if form.payment_method is SubmissionMethod.CASH else None,
        screenshot_file_id=stored.id if stored else None,
        submitter_ip=ip,
    )
    db.add(submission)
    await db.flush()
    await notifications.notify_admins(
        db,
        NotificationType.PAYMENT,
        "New payment submitted",
        f"₹{form.amount:,.0f} for {form.student_name} via {form.payment_method.value} "
        f"({submission.submission_number}).",
        link="/super-admin/payments",
        related_id=submission.id,
    )
    await db.commit()
    return submission


async def _payment_ids(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not ids:
        return {}
    rows = await db.execute(select(Payment.submission_id, Payment.id).where(Payment.submission_id.in_(ids)))
    return {sid: pid for sid, pid in rows.all() if sid}


def _out(sub: PaymentSubmission, payment_id: uuid.UUID | None) -> SubmissionOut:
    return SubmissionOut(
        id=sub.id,
        submission_number=sub.submission_number,
        student_name=sub.student_name,
        parent_mobile=sub.parent_mobile,
        amount=sub.amount,
        payment_date=sub.payment_date,
        payment_method=sub.payment_method,
        transaction_reference=sub.transaction_reference,
        handed_over_to=sub.handed_over_to,
        screenshot=sub.screenshot,
        status=sub.status,
        rejection_reason=sub.rejection_reason,
        remarks=sub.remarks,
        reviewed_by=sub.reviewed_by.full_name if sub.reviewed_by else None,
        reviewed_at=sub.reviewed_at,
        payment_id=payment_id,
        created_at=sub.created_at,
    )


async def list_submissions(
    db: AsyncSession, params: PageParams, *, status: SubmissionStatus | None, search: str | None
) -> tuple[list[SubmissionOut], int]:
    stmt = select(PaymentSubmission)
    if status:
        stmt = stmt.where(PaymentSubmission.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                PaymentSubmission.student_name.ilike(like),
                PaymentSubmission.submission_number.ilike(like),
                PaymentSubmission.transaction_reference.ilike(like),
                PaymentSubmission.parent_mobile.like(like),
            )
        )
    stmt = stmt.order_by(PaymentSubmission.created_at.desc())
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    subs = list(await db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    payments = await _payment_ids(db, [s.id for s in subs])
    return [_out(s, payments.get(s.id)) for s in subs], total


async def _get(db: AsyncSession, submission_id: uuid.UUID, *, lock: bool = False) -> PaymentSubmission:
    stmt = select(PaymentSubmission).where(PaymentSubmission.id == submission_id)
    if lock:
        stmt = stmt.with_for_update()  # two admins cannot verify the same submission twice
    sub = await db.scalar(stmt)
    if sub is None:
        raise NotFound("Submission not found", code="SUBMISSION_NOT_FOUND")
    return sub


async def submission_detail(db: AsyncSession, submission_id: uuid.UUID) -> SubmissionOut:
    sub = await _get(db, submission_id)
    payments = await _payment_ids(db, [sub.id])
    candidates = await db.scalars(
        select(Student).where(Student.parent_phone == sub.parent_mobile).order_by(Student.full_name).limit(10)
    )
    out = _out(sub, payments.get(sub.id))
    out.candidate_students = [
        CandidateStudent(
            id=s.id,
            student_code=s.student_code,
            full_name=s.full_name,
            parent_name=s.parent_name,
            parent_phone=s.parent_phone,
            category=RefItem.model_validate(s.category),
        )
        for s in candidates
    ]
    return out


def _require_pending(sub: PaymentSubmission) -> None:
    if sub.status is not SubmissionStatus.PENDING:
        raise Conflict("This submission has already been reviewed", code="SUBMISSION_ALREADY_REVIEWED")


async def update_submission(
    db: AsyncSession, submission_id: uuid.UUID, data: SubmissionPatch
) -> SubmissionOut:
    sub = await _get(db, submission_id, lock=True)
    _require_pending(sub)
    values = data.model_dump(exclude_unset=True)
    if values.get("student_name"):
        sub.student_name = values["student_name"]
    if "remarks" in values:
        sub.remarks = values["remarks"]
    await db.commit()
    return await submission_detail(db, submission_id)


async def verify(db: AsyncSession, submission_id: uuid.UUID, data: VerifyIn, actor: User) -> SubmissionOut:
    sub = await _get(db, submission_id, lock=True)
    _require_pending(sub)
    allocated = sum((a.amount for a in data.allocations), Decimal(0))
    if allocated != sub.amount:
        raise BusinessRuleViolation(
            f"Allocations (₹{allocated}) must add up to the submitted amount (₹{sub.amount})",
            code="ALLOCATION_MISMATCH",
        )
    await fees.record_payment(
        db,
        student_id=data.student_id,
        paid_on=sub.payment_date,
        mode=PaymentMode.UPI if sub.payment_method is SubmissionMethod.UPI else PaymentMode.CASH,
        allocations=data.allocations,
        discount=data.discount,
        reference=sub.transaction_reference or sub.submission_number,
        remarks=f"Parent submission {sub.submission_number}",
        actor=actor,
        source=PaymentSource.SUBMISSION,
        submission_id=sub.id,
    )
    sub.status = SubmissionStatus.VERIFIED
    sub.reviewed_by_id = actor.id
    sub.reviewed_at = utcnow()
    audit.record(
        db,
        "SUBMISSION_VERIFIED",
        actor_user_id=actor.id,
        entity_type="payment_submission",
        entity_id=sub.id,
        changes={"student_id": str(data.student_id)},
    )
    await db.commit()
    return await submission_detail(db, submission_id)


async def reject(db: AsyncSession, submission_id: uuid.UUID, reason: str, actor: User) -> SubmissionOut:
    sub = await _get(db, submission_id, lock=True)
    _require_pending(sub)
    sub.status = SubmissionStatus.REJECTED
    sub.rejection_reason = reason
    sub.reviewed_by_id = actor.id
    sub.reviewed_at = utcnow()
    audit.record(
        db,
        "SUBMISSION_REJECTED",
        actor_user_id=actor.id,
        entity_type="payment_submission",
        entity_id=sub.id,
        changes={"reason": reason},
    )
    await db.commit()
    return await submission_detail(db, submission_id)
