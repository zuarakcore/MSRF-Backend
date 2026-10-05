import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, Response, UploadFile, status

from app.api.deps import AdminUser, DbSession, require_admin
from app.core.captcha import verify_captcha
from app.core.enums import RecordStatus
from app.core.pagination import Page, Pagination, build_page
from app.core.rate_limit import RateLimit, client_ip
from app.core.schemas import MessageOut
from app.modules.website import service
from app.modules.website.models import (
    ApplicationStatus,
    EnquiryStatus,
    GalleryItem,
    JobStatus,
    Programme,
    TeamMember,
)
from app.modules.website.schemas import (
    ApplicationForm,
    ApplicationOut,
    ApplicationPatch,
    EnquiryIn,
    EnquiryOut,
    EnquiryPatch,
    GalleryItemOut,
    GalleryPatch,
    JobIn,
    JobOut,
    JobPatch,
    OrderIn,
    ProgrammeIn,
    ProgrammeOut,
    ProgrammePatch,
    PublicGalleryItem,
    PublicJob,
    PublicProgramme,
    PublicTeamMember,
    TeamMemberIn,
    TeamMemberOut,
    TeamMemberPatch,
)

Search = Annotated[str | None, Query(max_length=100)]
StatusQ = Annotated[RecordStatus | None, Query(alias="status")]
NO_CONTENT = status.HTTP_204_NO_CONTENT
CREATED = status.HTTP_201_CREATED


def _no_content() -> Response:
    return Response(status_code=NO_CONTENT)


admin = APIRouter(tags=["Website CMS"], dependencies=[Depends(require_admin)])

# --- programmes ----------------------------------------------------------------------------


@admin.get("/programmes", response_model=list[ProgrammeOut], summary="Programmes (with enquiry counts)")
async def list_programmes(
    db: DbSession, status_filter: StatusQ = None, search: Search = None
) -> list[ProgrammeOut]:
    return await service.list_programmes(db, status=status_filter, search=search)


@admin.post("/programmes", response_model=ProgrammeOut, status_code=CREATED, summary="Create a programme")
async def create_programme(body: ProgrammeIn, db: DbSession) -> ProgrammeOut:
    return await service.create_programme(db, body)


@admin.put("/programmes/order", status_code=NO_CONTENT, summary="Set display order (ids in order)")
async def order_programmes(body: OrderIn, db: DbSession) -> Response:
    await service.reorder(db, Programme, body.ids)
    return _no_content()


@admin.patch("/programmes/{programme_id}", response_model=ProgrammeOut, summary="Update a programme")
async def update_programme(programme_id: uuid.UUID, body: ProgrammePatch, db: DbSession) -> ProgrammeOut:
    return await service.update_programme(db, programme_id, body)


@admin.delete("/programmes/{programme_id}", status_code=NO_CONTENT, summary="Delete a programme")
async def delete_programme(programme_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_programme(db, programme_id)
    return _no_content()


# --- team ----------------------------------------------------------------------------------


@admin.get("/team-members", response_model=list[TeamMemberOut], summary="Leadership / board members")
async def list_team(
    db: DbSession, status_filter: StatusQ = None, search: Search = None
) -> list[TeamMemberOut]:
    return await service.list_team(db, status=status_filter, search=search)


@admin.get("/team-members/designations", response_model=list[str], summary="Designations in use")
async def designations(db: DbSession) -> list[str]:
    return await service.designations(db)


@admin.post("/team-members", response_model=TeamMemberOut, status_code=CREATED, summary="Add a member")
async def create_member(body: TeamMemberIn, db: DbSession) -> TeamMemberOut:
    return await service.create_member(db, body)


@admin.put("/team-members/order", status_code=NO_CONTENT, summary="Set display order")
async def order_team(body: OrderIn, db: DbSession) -> Response:
    await service.reorder(db, TeamMember, body.ids)
    return _no_content()


@admin.patch("/team-members/{member_id}", response_model=TeamMemberOut, summary="Update a member")
async def update_member(member_id: uuid.UUID, body: TeamMemberPatch, db: DbSession) -> TeamMemberOut:
    return await service.update_member(db, member_id, body)


@admin.delete("/team-members/{member_id}", status_code=NO_CONTENT, summary="Delete a member")
async def delete_member(member_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_member(db, member_id)
    return _no_content()


# --- gallery -------------------------------------------------------------------------------


@admin.get("/gallery-items", response_model=Page[GalleryItemOut], summary="Gallery photos")
async def list_gallery(
    db: DbSession,
    pagination: Pagination,
    category: Annotated[str | None, Query(max_length=40)] = None,
    status_filter: StatusQ = None,
    search: Search = None,
) -> Page[GalleryItemOut]:
    items, total = await service.list_gallery(
        db, pagination, category=category, status=status_filter, search=search
    )
    return build_page(items, total, pagination)


@admin.get("/gallery-items/categories", response_model=list[str], summary="Categories in use")
async def gallery_categories(db: DbSession) -> list[str]:
    return await service.gallery_categories(db, active_only=False)


@admin.post(
    "/gallery-items",
    response_model=GalleryItemOut,
    status_code=CREATED,
    summary="Upload a photo (JPEG, PNG, WebP; max 10 MB). A thumbnail is generated.",
)
async def create_gallery_item(
    db: DbSession,
    user: AdminUser,
    title: Annotated[str, Form(min_length=1, max_length=120)],
    category: Annotated[str, Form(min_length=1, max_length=40)],
    image: Annotated[UploadFile, File()],
    caption: Annotated[str | None, Form(max_length=300)] = None,
    is_wide: Annotated[bool, Form(alias="isWide")] = False,
) -> GalleryItemOut:
    return await service.create_gallery_item(
        db,
        title=title.strip(),
        caption=(caption or "").strip() or None,
        category=category.strip(),
        is_wide=is_wide,
        upload=image,
        actor_id=user.id,
    )


@admin.patch("/gallery-items/{item_id}", response_model=GalleryItemOut, summary="Update photo details")
async def update_gallery_item(item_id: uuid.UUID, body: GalleryPatch, db: DbSession) -> GalleryItemOut:
    return await service.update_gallery_item(db, item_id, body)


@admin.put("/gallery-items/order", status_code=NO_CONTENT, summary="Set display order")
async def order_gallery(body: OrderIn, db: DbSession) -> Response:
    await service.reorder(db, GalleryItem, body.ids)
    return _no_content()


@admin.patch("/gallery-items/{item_id}/image", response_model=GalleryItemOut, summary="Replace the photo")
async def replace_gallery_image(
    item_id: uuid.UUID, db: DbSession, user: AdminUser, image: Annotated[UploadFile, File()]
) -> GalleryItemOut:
    return await service.replace_gallery_image(db, item_id, image, user.id)


@admin.delete("/gallery-items/{item_id}", status_code=NO_CONTENT, summary="Delete a photo")
async def delete_gallery_item(item_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_gallery_item(db, item_id)
    return _no_content()


# --- jobs and applications ---------------------------------------------------------------


@admin.get("/jobs", response_model=list[JobOut], summary="Job postings (with application counts)")
async def list_jobs(
    db: DbSession,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    search: Search = None,
) -> list[JobOut]:
    return await service.list_jobs(db, status=status_filter, search=search)


@admin.post("/jobs", response_model=JobOut, status_code=CREATED, summary="Post a job")
async def create_job(body: JobIn, db: DbSession) -> JobOut:
    return await service.create_job(db, body)


@admin.patch(
    "/jobs/{job_id}", response_model=JobOut, summary="Update a job (status CLOSED to stop applications)"
)
async def update_job(job_id: uuid.UUID, body: JobPatch, db: DbSession) -> JobOut:
    return await service.update_job(db, job_id, body)


@admin.delete("/jobs/{job_id}", status_code=NO_CONTENT, summary="Delete a job without applications")
async def delete_job(job_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_job(db, job_id)
    return _no_content()


@admin.get("/job-applications", response_model=Page[ApplicationOut], summary="Job applications")
async def list_applications(
    db: DbSession,
    pagination: Pagination,
    job_id: Annotated[uuid.UUID | None, Query(alias="jobId")] = None,
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    search: Search = None,
) -> Page[ApplicationOut]:
    items, total = await service.list_applications(
        db, pagination, job_id=job_id, status=status_filter, search=search
    )
    return build_page(items, total, pagination)


@admin.get(
    "/job-applications/{application_id}", response_model=ApplicationOut, summary="Application (CV link)"
)
async def get_application(application_id: uuid.UUID, db: DbSession) -> ApplicationOut:
    return service.application_out(await service.get_application(db, application_id))


@admin.patch(
    "/job-applications/{application_id}", response_model=ApplicationOut, summary="Update status/details"
)
async def update_application(
    application_id: uuid.UUID, body: ApplicationPatch, db: DbSession
) -> ApplicationOut:
    return await service.update_application(db, application_id, body)


@admin.delete("/job-applications/{application_id}", status_code=NO_CONTENT, summary="Delete (and its CV)")
async def delete_application(application_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_application(db, application_id)
    return _no_content()


# --- enquiries -----------------------------------------------------------------------------


@admin.get("/enquiries", response_model=Page[EnquiryOut], summary="Contact enquiries")
async def list_enquiries(
    db: DbSession,
    pagination: Pagination,
    status_filter: Annotated[EnquiryStatus | None, Query(alias="status")] = None,
    programme_id: Annotated[uuid.UUID | None, Query(alias="programmeId")] = None,
    search: Search = None,
) -> Page[EnquiryOut]:
    items, total = await service.list_enquiries(
        db, pagination, status=status_filter, programme_id=programme_id, search=search
    )
    return build_page(items, total, pagination)


@admin.patch("/enquiries/{enquiry_id}", response_model=EnquiryOut, summary="Update status/details")
async def update_enquiry(enquiry_id: uuid.UUID, body: EnquiryPatch, db: DbSession) -> EnquiryOut:
    return await service.update_enquiry(db, enquiry_id, body)


@admin.delete("/enquiries/{enquiry_id}", status_code=NO_CONTENT, summary="Delete an enquiry")
async def delete_enquiry(enquiry_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_enquiry(db, enquiry_id)
    return _no_content()


# --- public (website) ----------------------------------------------------------------------

public = APIRouter(
    prefix="/public",
    tags=["Public Website"],
    dependencies=[Depends(RateLimit("public-read", "120/minute", fail_open=True))],
)


@public.get("/programmes", response_model=list[PublicProgramme], summary="Active programmes")
async def public_programmes(db: DbSession) -> list[PublicProgramme]:
    return await service.public_programmes(db)


@public.get("/programmes/{slug}", response_model=PublicProgramme, summary="One programme by slug")
async def public_programme(slug: Annotated[str, Path(max_length=140)], db: DbSession) -> PublicProgramme:
    return await service.public_programme(db, slug)


@public.get("/team-members", response_model=list[PublicTeamMember], summary="Leadership")
async def public_team(db: DbSession) -> list[PublicTeamMember]:
    return await service.public_team(db)


@public.get("/gallery-items", response_model=Page[PublicGalleryItem], summary="Gallery (paginated)")
async def public_gallery(
    db: DbSession, pagination: Pagination, category: Annotated[str | None, Query(max_length=40)] = None
) -> Page[PublicGalleryItem]:
    items, total = await service.public_gallery(db, pagination, category)
    return build_page(items, total, pagination)


@public.get("/gallery-categories", response_model=list[str], summary="Gallery categories with active photos")
async def public_gallery_categories(db: DbSession) -> list[str]:
    return await service.gallery_categories(db, active_only=True)


@public.get("/jobs", response_model=list[PublicJob], summary="Open positions")
async def public_jobs(db: DbSession) -> list[PublicJob]:
    return await service.public_jobs(db)


@public.post(
    "/jobs/{job_id}/applications",
    response_model=MessageOut,
    status_code=CREATED,
    dependencies=[Depends(RateLimit("public-apply-ip", "5/hour"))],
    summary="Apply for a job (multipart; CV as PDF/DOC/DOCX, max 5 MB)",
)
async def apply(
    job_id: uuid.UUID,
    request: Request,
    db: DbSession,
    form: Annotated[ApplicationForm, Form()],
) -> MessageOut:
    ip = client_ip(request)
    await verify_captcha(form.captcha_token, ip)
    await service.apply(db, job_id, form, form.cv, ip)
    return MessageOut(detail="Application received. Our team will review your CV and get back to you.")


@public.post(
    "/enquiries",
    response_model=MessageOut,
    status_code=CREATED,
    dependencies=[Depends(RateLimit("public-enquiry-ip", "5/hour"))],
    summary="Contact form",
)
async def create_enquiry(body: EnquiryIn, request: Request, db: DbSession) -> MessageOut:
    ip = client_ip(request)
    await verify_captcha(body.captcha_token, ip)
    await service.create_enquiry(db, body, ip)
    return MessageOut(detail="Thanks — we'll be in touch soon.")
