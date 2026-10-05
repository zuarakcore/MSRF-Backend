import re
import unicodedata
import uuid
from typing import Any

from fastapi import UploadFile
from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.email import queue_email
from app.core.enums import RecordStatus
from app.core.errors import AppError, BusinessRuleViolation, Conflict, NotFound
from app.core.pagination import PageParams
from app.core.rate_limit import enforce
from app.core.search import contains
from app.core.timeutils import today_local
from app.modules.files import service as files
from app.modules.files.models import UPLOAD_POLICIES, FilePurpose
from app.modules.files.schemas import file_url
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationType
from app.modules.website.models import (
    Enquiry,
    GalleryItem,
    Job,
    JobApplication,
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

SortableModel = type[Programme] | type[TeamMember] | type[GalleryItem]


def _apply(obj: Any, values: dict[str, Any], required: set[str]) -> None:
    for field, value in values.items():
        if value is None and field in required:
            continue  # required columns cannot be cleared with null
        setattr(obj, field, value)


async def reorder(db: AsyncSession, model: SortableModel, ids: list[uuid.UUID]) -> None:
    for position, item_id in enumerate(ids):
        await db.execute(update(model).where(model.id == item_id).values(sort_order=position))
    await db.commit()


# --- programmes ----------------------------------------------------------------------------


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:120] or "programme"


async def _unique_slug(db: AsyncSession, title: str) -> str:
    base = slugify(title)
    slug, n = base, 2
    while await db.scalar(select(Programme.id).where(Programme.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


async def list_programmes(
    db: AsyncSession, *, status: RecordStatus | None, search: str | None
) -> list[ProgrammeOut]:
    counts = select(Enquiry.programme_id, func.count().label("n")).group_by(Enquiry.programme_id).subquery()
    stmt = (
        select(Programme, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.programme_id == Programme.id)
        .order_by(Programme.sort_order, Programme.date.asc().nullslast(), Programme.title)
    )
    if status:
        stmt = stmt.where(Programme.status == status)
    if search:
        stmt = stmt.where(Programme.title.ilike(contains(search)))
    return [
        ProgrammeOut.model_validate(p).model_copy(update={"enquiries_count": n})
        for p, n in (await db.execute(stmt)).all()
    ]


async def get_programme(db: AsyncSession, programme_id: uuid.UUID) -> Programme:
    programme = await db.get(Programme, programme_id)
    if programme is None:
        raise NotFound("Programme not found", code="PROGRAMME_NOT_FOUND")
    return programme


async def create_programme(db: AsyncSession, data: ProgrammeIn) -> ProgrammeOut:
    programme = Programme(**data.model_dump(), slug=await _unique_slug(db, data.title))
    db.add(programme)
    await db.commit()
    return ProgrammeOut.model_validate(programme)


async def update_programme(db: AsyncSession, programme_id: uuid.UUID, data: ProgrammePatch) -> ProgrammeOut:
    programme = await get_programme(db, programme_id)
    # The slug stays stable after creation so published links keep working.
    _apply(
        programme,
        data.model_dump(exclude_unset=True),
        {"title", "description", "benefits", "status", "sort_order"},
    )
    await db.commit()
    await db.refresh(programme)
    return ProgrammeOut.model_validate(programme)


async def delete_programme(db: AsyncSession, programme_id: uuid.UUID) -> None:
    await db.delete(await get_programme(db, programme_id))  # enquiries keep their subject snapshot
    await db.commit()


async def public_programmes(db: AsyncSession) -> list[PublicProgramme]:
    rows = await db.scalars(
        select(Programme)
        .where(Programme.status == RecordStatus.ACTIVE)
        .order_by(Programme.sort_order, Programme.date.asc().nullslast(), Programme.title)
    )
    return [PublicProgramme.model_validate(p) for p in rows]


async def public_programme(db: AsyncSession, slug: str) -> PublicProgramme:
    programme = await db.scalar(
        select(Programme).where(Programme.slug == slug, Programme.status == RecordStatus.ACTIVE)
    )
    if programme is None:
        raise NotFound("Programme not found", code="PROGRAMME_NOT_FOUND")
    return PublicProgramme.model_validate(programme)


# --- team ----------------------------------------------------------------------------------


async def get_member(db: AsyncSession, member_id: uuid.UUID) -> TeamMember:
    member = await db.get(TeamMember, member_id)
    if member is None:
        raise NotFound("Team member not found", code="TEAM_MEMBER_NOT_FOUND")
    return member


async def list_team(
    db: AsyncSession, *, status: RecordStatus | None, search: str | None
) -> list[TeamMemberOut]:
    stmt = select(TeamMember).order_by(TeamMember.sort_order, TeamMember.name)
    if status:
        stmt = stmt.where(TeamMember.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(or_(TeamMember.name.ilike(like), TeamMember.designation.ilike(like)))
    return [TeamMemberOut.model_validate(m) for m in await db.scalars(stmt)]


async def create_member(db: AsyncSession, data: TeamMemberIn) -> TeamMemberOut:
    values = data.model_dump()
    photo = await files.get_file_of_purpose(db, values.pop("photo_file_id"), FilePurpose.TEAM_PHOTO)
    member = TeamMember(**values, photo_file_id=photo.id if photo else None)
    member.designation = member.designation.upper()
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return TeamMemberOut.model_validate(member)


async def update_member(db: AsyncSession, member_id: uuid.UUID, data: TeamMemberPatch) -> TeamMemberOut:
    member = await get_member(db, member_id)
    values = data.model_dump(exclude_unset=True)
    if "photo_file_id" in values:
        photo = await files.get_file_of_purpose(db, values.pop("photo_file_id"), FilePurpose.TEAM_PHOTO)
        member.photo_file_id = photo.id if photo else None
    if values.get("designation"):
        values["designation"] = values["designation"].upper()
    _apply(member, values, {"name", "designation", "status", "sort_order"})
    await db.commit()
    await db.refresh(member)
    return TeamMemberOut.model_validate(member)


async def delete_member(db: AsyncSession, member_id: uuid.UUID) -> None:
    await db.delete(await get_member(db, member_id))
    await db.commit()


async def designations(db: AsyncSession) -> list[str]:
    return list(await db.scalars(select(TeamMember.designation).distinct().order_by(TeamMember.designation)))


async def public_team(db: AsyncSession) -> list[PublicTeamMember]:
    rows = await db.scalars(
        select(TeamMember)
        .where(TeamMember.status == RecordStatus.ACTIVE)
        .order_by(TeamMember.sort_order, TeamMember.name)
    )
    return [
        PublicTeamMember(
            id=m.id,
            name=m.name,
            designation=m.designation,
            biography=m.biography,
            photo_url=file_url(m.photo) if m.photo else get_settings().default_image_url,
        )
        for m in rows
    ]


# --- gallery -------------------------------------------------------------------------------


async def _store_gallery_image(db: AsyncSession, upload: UploadFile, actor_id: uuid.UUID) -> tuple[Any, Any]:
    data = await files.read_limited(upload, UPLOAD_POLICIES[FilePurpose.GALLERY_IMAGE].max_bytes)
    image = await files.store_bytes(
        db,
        data,
        purpose=FilePurpose.GALLERY_IMAGE,
        original_filename=upload.filename,
        uploaded_by_id=actor_id,
    )
    thumb = await files.store_bytes(
        db,
        data,
        purpose=FilePurpose.GALLERY_THUMBNAIL,
        original_filename=upload.filename,
        uploaded_by_id=actor_id,
    )
    return image, thumb


async def get_gallery_item(db: AsyncSession, item_id: uuid.UUID) -> GalleryItem:
    item = await db.get(GalleryItem, item_id)
    if item is None:
        raise NotFound("Gallery item not found", code="GALLERY_ITEM_NOT_FOUND")
    return item


def _gallery_query(*, category: str | None, status: RecordStatus | None, search: str | None) -> Select[Any]:
    stmt = select(GalleryItem).order_by(GalleryItem.sort_order, GalleryItem.created_at.desc())
    if category:
        stmt = stmt.where(func.lower(GalleryItem.category) == category.lower())
    if status:
        stmt = stmt.where(GalleryItem.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                GalleryItem.title.ilike(like),
                GalleryItem.caption.ilike(like),
                GalleryItem.category.ilike(like),
            )
        )
    return stmt


async def list_gallery(
    db: AsyncSession, params: PageParams, **filters: Any
) -> tuple[list[GalleryItemOut], int]:
    stmt = _gallery_query(**filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [GalleryItemOut.model_validate(i) for i in items], total


async def create_gallery_item(
    db: AsyncSession,
    *,
    title: str,
    caption: str | None,
    category: str,
    is_wide: bool,
    upload: UploadFile,
    actor_id: uuid.UUID,
) -> GalleryItemOut:
    image, thumb = await _store_gallery_image(db, upload, actor_id)
    item = GalleryItem(
        title=title,
        caption=caption,
        category=category,
        is_wide=is_wide,
        image_file_id=image.id,
        thumbnail_file_id=thumb.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return GalleryItemOut.model_validate(item)


async def update_gallery_item(db: AsyncSession, item_id: uuid.UUID, data: GalleryPatch) -> GalleryItemOut:
    item = await get_gallery_item(db, item_id)
    _apply(
        item, data.model_dump(exclude_unset=True), {"title", "category", "is_wide", "status", "sort_order"}
    )
    await db.commit()
    await db.refresh(item)
    return GalleryItemOut.model_validate(item)


async def replace_gallery_image(
    db: AsyncSession, item_id: uuid.UUID, upload: UploadFile, actor_id: uuid.UUID
) -> GalleryItemOut:
    item = await get_gallery_item(db, item_id)
    old = [item.image, item.thumbnail]
    image, thumb = await _store_gallery_image(db, upload, actor_id)
    item.image_file_id, item.thumbnail_file_id = image.id, thumb.id
    await db.flush()
    pending = [await files.delete_stored_file(db, f) for f in old if f is not None]
    await db.commit()
    await files.purge_objects(pending)
    await db.refresh(item)
    return GalleryItemOut.model_validate(item)


async def delete_gallery_item(db: AsyncSession, item_id: uuid.UUID) -> None:
    item = await get_gallery_item(db, item_id)
    stored = [item.image, item.thumbnail]
    await db.delete(item)
    await db.flush()
    pending = [await files.delete_stored_file(db, f) for f in stored if f is not None]
    await db.commit()
    await files.purge_objects(pending)


async def gallery_categories(db: AsyncSession, *, active_only: bool) -> list[str]:
    stmt = select(GalleryItem.category).distinct().order_by(GalleryItem.category)
    if active_only:
        stmt = stmt.where(GalleryItem.status == RecordStatus.ACTIVE)
    return list(await db.scalars(stmt))


def public_gallery_item(item: GalleryItem) -> PublicGalleryItem:
    return PublicGalleryItem(
        id=item.id,
        title=item.title,
        caption=item.caption,
        category=item.category,
        is_wide=item.is_wide,
        image_url=file_url(item.image),
        thumbnail_url=file_url(item.thumbnail) if item.thumbnail else file_url(item.image),
        width=item.image.width,
        height=item.image.height,
    )


async def public_gallery(
    db: AsyncSession, params: PageParams, category: str | None
) -> tuple[list[PublicGalleryItem], int]:
    stmt = _gallery_query(category=category, status=RecordStatus.ACTIVE, search=None)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [public_gallery_item(i) for i in items], total


# --- jobs ----------------------------------------------------------------------------------


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found", code="JOB_NOT_FOUND")
    return job


async def list_jobs(db: AsyncSession, *, status: JobStatus | None, search: str | None) -> list[JobOut]:
    counts = select(JobApplication.job_id, func.count().label("n")).group_by(JobApplication.job_id).subquery()
    stmt = (
        select(Job, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.job_id == Job.id)
        .order_by(Job.posted_on.desc())
    )
    if status:
        stmt = stmt.where(Job.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(or_(Job.title.ilike(like), Job.location.ilike(like)))
    return [
        JobOut.model_validate(j).model_copy(update={"applications_count": n})
        for j, n in (await db.execute(stmt)).all()
    ]


async def create_job(db: AsyncSession, data: JobIn) -> JobOut:
    job = Job(**data.model_dump())
    db.add(job)
    await db.commit()
    return JobOut.model_validate(job)


async def update_job(db: AsyncSession, job_id: uuid.UUID, data: JobPatch) -> JobOut:
    job = await get_job(db, job_id)
    _apply(
        job, data.model_dump(exclude_unset=True), {"title", "location", "description", "posted_on", "status"}
    )
    if job.closing_date and job.closing_date < job.posted_on:
        raise BusinessRuleViolation("Closing date must be on or after the posted date", code="INVALID_DATES")
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job)


async def delete_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    job = await get_job(db, job_id)
    if await db.scalar(select(func.count()).where(JobApplication.job_id == job_id)):
        raise Conflict("This job has applications. Close it instead.", code="IN_USE")
    await db.delete(job)
    await db.commit()


def _open_jobs() -> Select[Any]:
    today = today_local()
    return select(Job).where(
        Job.status == JobStatus.OPEN,
        Job.posted_on <= today,
        or_(Job.closing_date.is_(None), Job.closing_date >= today),
    )


async def public_jobs(db: AsyncSession) -> list[PublicJob]:
    return [
        PublicJob.model_validate(j) for j in await db.scalars(_open_jobs().order_by(Job.posted_on.desc()))
    ]


# --- applications ------------------------------------------------------------------------


def application_out(app_: JobApplication) -> ApplicationOut:
    return ApplicationOut(
        id=app_.id,
        job_id=app_.job_id,
        position=app_.job.title,
        full_name=app_.full_name,
        email=app_.email,
        phone=app_.phone,
        location=app_.location,
        cv=app_.cv,
        status=app_.status,
        created_at=app_.created_at,
    )


async def apply(
    db: AsyncSession, job_id: uuid.UUID, form: ApplicationForm, cv: UploadFile, ip: str | None
) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found", code="JOB_NOT_FOUND")
    if await db.scalar(_open_jobs().where(Job.id == job_id)) is None:
        raise Conflict("Applications for this position are closed", code="JOB_CLOSED")
    email = str(form.email_address).lower()
    already = await db.scalar(
        select(JobApplication.id).where(JobApplication.job_id == job_id, JobApplication.email == email)
    )
    if already:
        raise Conflict("You have already applied for this position", code="ALREADY_APPLIED")
    stored = await files.store_upload(db, cv, purpose=FilePurpose.RESUME, uploaded_by_id=None)
    application = JobApplication(
        job_id=job_id,
        full_name=form.full_name,
        email=str(form.email_address).lower(),
        phone=form.mobile_number,
        location=form.location,
        cv_file_id=stored.id,
        submitter_ip=ip,
    )
    db.add(application)
    await db.flush()
    await notifications.notify_admins(
        db,
        NotificationType.APPLICATION,
        "New job application",
        f"{form.full_name} applied for {job.title}.",
        link="/super-admin/website/job-applications",
        related_id=application.id,
    )
    await db.commit()


async def get_application(db: AsyncSession, application_id: uuid.UUID) -> JobApplication:
    application = await db.get(JobApplication, application_id)
    if application is None:
        raise NotFound("Application not found", code="APPLICATION_NOT_FOUND")
    return application


async def list_applications(
    db: AsyncSession, params: PageParams, *, job_id: uuid.UUID | None, status: Any, search: str | None
) -> tuple[list[ApplicationOut], int]:
    stmt = select(JobApplication).join(Job, Job.id == JobApplication.job_id)
    if job_id:
        stmt = stmt.where(JobApplication.job_id == job_id)
    if status:
        stmt = stmt.where(JobApplication.status == status)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(JobApplication.full_name.ilike(like), JobApplication.email.ilike(like), Job.title.ilike(like))
        )
    stmt = stmt.order_by(JobApplication.created_at.desc())
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [application_out(a) for a in rows], total


async def update_application(
    db: AsyncSession, application_id: uuid.UUID, data: ApplicationPatch
) -> ApplicationOut:
    application = await get_application(db, application_id)
    values = data.model_dump(exclude_unset=True)
    if values.get("email"):
        values["email"] = str(values["email"]).lower()
    _apply(application, values, {"status", "full_name", "email", "phone"})
    await db.commit()
    await db.refresh(application)
    return application_out(application)


async def delete_application(db: AsyncSession, application_id: uuid.UUID) -> None:
    application = await get_application(db, application_id)
    cv = application.cv
    await db.delete(application)
    await db.flush()
    pending = await files.delete_stored_file(db, cv)
    await db.commit()
    await files.purge_objects([pending])


# --- enquiries -----------------------------------------------------------------------------


async def create_enquiry(db: AsyncSession, data: EnquiryIn, ip: str | None) -> None:
    programme = None
    if data.programme_id:
        programme = await db.scalar(
            select(Programme).where(
                Programme.id == data.programme_id, Programme.status == RecordStatus.ACTIVE
            )
        )
        if programme is None:
            raise BusinessRuleViolation("Programme not found", code="PROGRAMME_NOT_FOUND")
    enquiry = Enquiry(
        parent_name=data.parent_name,
        player_name=data.player_name,
        phone=data.phone,
        email=str(data.email).lower() if data.email else None,
        programme_id=programme.id if programme else None,
        subject=f"{programme.title} ({programme.age_group})" if programme else None,
        message=data.message,
        submitter_ip=ip,
    )
    db.add(enquiry)
    await db.flush()
    await notifications.notify_admins(
        db,
        NotificationType.ENQUIRY,
        "New website enquiry",
        f"{data.parent_name}" + (f" about {programme.title}" if programme else "") + ".",
        link="/super-admin/website/enquiries",
        related_id=enquiry.id,
    )
    await db.commit()
    if enquiry.email and await _auto_reply_allowed(enquiry.email):
        # The email contains no text typed by the visitor (only the programme title from our own
        # database), so the form cannot be used to send arbitrary content from our address.
        queue_email("enquiry_received", enquiry.email, {"subject": enquiry.subject or "your enquiry"})


async def _auto_reply_allowed(email: str) -> bool:
    """At most 2 auto-replies per address per day, so the form cannot be used to mail-bomb someone."""
    try:
        await enforce("enquiry-auto-reply", "2/day", email.lower())
    except AppError:
        return False
    return True


async def get_enquiry(db: AsyncSession, enquiry_id: uuid.UUID) -> Enquiry:
    enquiry = await db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise NotFound("Enquiry not found", code="ENQUIRY_NOT_FOUND")
    return enquiry


async def list_enquiries(
    db: AsyncSession, params: PageParams, *, status: Any, programme_id: uuid.UUID | None, search: str | None
) -> tuple[list[EnquiryOut], int]:
    stmt = select(Enquiry)
    if status:
        stmt = stmt.where(Enquiry.status == status)
    if programme_id:
        stmt = stmt.where(Enquiry.programme_id == programme_id)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                Enquiry.parent_name.ilike(like),
                Enquiry.player_name.ilike(like),
                Enquiry.email.ilike(like),
                Enquiry.phone.like(like),
                Enquiry.subject.ilike(like),
            )
        )
    stmt = stmt.order_by(Enquiry.created_at.desc())
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = await db.scalars(stmt.limit(params.page_size).offset(params.offset))
    return [EnquiryOut.model_validate(e) for e in rows], total


async def update_enquiry(db: AsyncSession, enquiry_id: uuid.UUID, data: EnquiryPatch) -> EnquiryOut:
    enquiry = await get_enquiry(db, enquiry_id)
    values = data.model_dump(exclude_unset=True)
    if values.get("email"):
        values["email"] = str(values["email"]).lower()
    _apply(enquiry, values, {"status", "parent_name", "phone"})
    await db.commit()
    await db.refresh(enquiry)
    return EnquiryOut.model_validate(enquiry)


async def delete_enquiry(db: AsyncSession, enquiry_id: uuid.UUID) -> None:
    await db.delete(await get_enquiry(db, enquiry_id))
    await db.commit()
