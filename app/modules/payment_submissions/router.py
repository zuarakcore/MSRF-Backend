import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, status

from app.api.deps import AdminUser, DbSession, require_admin
from app.core.captcha import verify_captcha
from app.core.pagination import Page, Pagination, build_page
from app.core.rate_limit import RateLimit, client_ip, enforce
from app.modules.payment_submissions import service
from app.modules.payment_submissions.models import SubmissionStatus
from app.modules.payment_submissions.schemas import (
    RejectIn,
    SubmissionCreated,
    SubmissionForm,
    SubmissionOut,
    SubmissionPatch,
    VerifyIn,
)

public_router = APIRouter(prefix="/public", tags=["Public Website"])


@public_router.post(
    "/payment-submissions",
    response_model=SubmissionCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimit("public-payment-ip", "5/hour"))],
    summary="Parent submits payment proof (multipart; screenshot required for UPI)",
)
async def submit_payment_proof(
    request: Request,
    db: DbSession,
    form: Annotated[SubmissionForm, Form()],
) -> SubmissionCreated:
    ip = client_ip(request)
    await enforce("public-payment-phone", "20/day", form.parent_mobile)
    await verify_captcha(form.captcha_token, ip)
    submission = await service.submit(db, form, form.screenshot, ip)
    return SubmissionCreated(submission_number=submission.submission_number, status=submission.status)


admin_router = APIRouter(
    prefix="/payment-submissions", tags=["Payment Verification"], dependencies=[Depends(require_admin)]
)


@admin_router.get("", response_model=Page[SubmissionOut], summary="Parent payment submissions")
async def list_submissions(
    db: DbSession,
    pagination: Pagination,
    status_filter: Annotated[SubmissionStatus | None, Query(alias="status")] = SubmissionStatus.PENDING,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[SubmissionOut]:
    items, total = await service.list_submissions(db, pagination, status=status_filter, search=search)
    return build_page(items, total, pagination)


@admin_router.get(
    "/{submission_id}",
    response_model=SubmissionOut,
    summary="Submission detail, with students whose parent phone matches (suggestions only)",
)
async def get_submission(submission_id: uuid.UUID, db: DbSession) -> SubmissionOut:
    return await service.submission_detail(db, submission_id)


@admin_router.patch("/{submission_id}", response_model=SubmissionOut, summary="Edit student name or remarks")
async def update_submission(submission_id: uuid.UUID, body: SubmissionPatch, db: DbSession) -> SubmissionOut:
    return await service.update_submission(db, submission_id, body)


@admin_router.post(
    "/{submission_id}/verify",
    response_model=SubmissionOut,
    summary="Verify: admin picks the student and month(s); creates a payment and receipt",
)
async def verify_submission(
    submission_id: uuid.UUID, body: VerifyIn, db: DbSession, admin: AdminUser
) -> SubmissionOut:
    return await service.verify(db, submission_id, body, admin)


@admin_router.post("/{submission_id}/reject", response_model=SubmissionOut, summary="Reject with a reason")
async def reject_submission(
    submission_id: uuid.UUID, body: RejectIn, db: DbSession, admin: AdminUser
) -> SubmissionOut:
    return await service.reject(db, submission_id, body.reason, admin)
