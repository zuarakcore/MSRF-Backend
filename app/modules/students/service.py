import uuid
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from fastapi import UploadFile
from sqlalchemy import Numeric, Select, cast, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.counters import next_code
from app.core.enums import AttendanceStatus, RecordStatus
from app.core.errors import Conflict, NotFound
from app.core.pagination import PageParams
from app.core.search import contains
from app.core.timeutils import month_start, today_local
from app.modules.fees import ledger
from app.modules.fees.models import FeeLedgerEntry, Payment
from app.modules.files import service as files
from app.modules.files.models import FilePurpose
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationType
from app.modules.performance.models import PerformanceReport
from app.modules.reference import service as reference
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.reference.schemas import RefItem
from app.modules.sessions.models import StudentAttendance
from app.modules.students.models import Student, StudentDocument
from app.modules.students.schemas import (
    FilterOptions,
    StudentDetail,
    StudentDocumentOut,
    StudentIn,
    StudentListItem,
    StudentPatch,
    StudentTotals,
)
from app.modules.users.models import User

ATTENDANCE_WINDOW_DAYS = 90
SortKey = Literal["fullName", "-admissionDate", "studentCode", "-createdAt"]
SORTS: dict[str, Any] = {
    "fullName": Student.full_name.asc(),
    "-admissionDate": Student.admission_date.desc(),
    "studentCode": Student.student_code.asc(),
    "-createdAt": Student.created_at.desc(),
}


async def get_student(db: AsyncSession, student_id: uuid.UUID) -> Student:
    student = await db.get(Student, student_id)
    if student is None:
        raise NotFound("Student not found", code="STUDENT_NOT_FOUND")
    return student


# --- listing ---------------------------------------------------------------------------


# Per-student metrics (attendance %, this month's fee status, fee totals) are computed only for the
# students actually being returned (a page of 20, or one profile), never for the whole roster.


def _attendance_subquery(ids: Sequence[uuid.UUID]) -> Any:
    since = today_local() - timedelta(days=ATTENDANCE_WINDOW_DAYS)
    return (
        select(
            StudentAttendance.student_id.label("student_id"),
            func.count().filter(StudentAttendance.status == AttendanceStatus.PRESENT).label("present"),
            func.count().label("total"),
        )
        .where(StudentAttendance.student_id.in_(ids), StudentAttendance.session_date >= since)
        .group_by(StudentAttendance.student_id)
        .subquery()
    )


def _current_fee_subquery(ids: Sequence[uuid.UUID]) -> Any:
    return (
        select(FeeLedgerEntry.student_id.label("student_id"), ledger.status_expr().label("fee_status"))
        .where(FeeLedgerEntry.student_id.in_(ids), FeeLedgerEntry.period == month_start(today_local()))
        .subquery()
    )


def _ledger_totals_subquery(ids: Sequence[uuid.UUID]) -> Any:
    e = FeeLedgerEntry
    return (
        select(
            e.student_id.label("student_id"),
            func.sum(e.amount_due).label("due"),
            func.sum(e.amount_paid).label("paid"),
            func.sum(e.discount).label("discount"),
        )
        .where(e.student_id.in_(ids))
        .group_by(e.student_id)
        .subquery()
    )


class StudentRow(NamedTuple):
    student: Student
    rate: Any
    fee_status: str | None
    due: Any
    paid: Any
    discount: Any


_NO_METRICS: tuple[Any, ...] = (None, None, 0, 0, 0)


async def _metrics(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[Any, ...]]:
    """(attendance rate, current fee status, due, paid, discount) for the given students: one query."""
    if not ids:
        return {}
    attendance = _attendance_subquery(ids)
    fees = _current_fee_subquery(ids)
    totals = _ledger_totals_subquery(ids)
    rate = func.round(cast(100 * attendance.c.present, Numeric) / func.nullif(attendance.c.total, 0), 1)
    stmt = (
        select(
            Student.id,
            rate,
            fees.c.fee_status,
            func.coalesce(totals.c.due, 0),
            func.coalesce(totals.c.paid, 0),
            func.coalesce(totals.c.discount, 0),
        )
        .outerjoin(attendance, attendance.c.student_id == Student.id)
        .outerjoin(fees, fees.c.student_id == Student.id)
        .outerjoin(totals, totals.c.student_id == Student.id)
        .where(Student.id.in_(ids))
    )
    return {row[0]: tuple(row[1:]) for row in (await db.execute(stmt)).all()}


async def _with_metrics(db: AsyncSession, students: Sequence[Student]) -> list[StudentRow]:
    metrics: dict[uuid.UUID, tuple[Any, ...]] = {}
    ids = [s.id for s in students]
    for i in range(0, len(ids), 1000):  # bounded IN lists for large exports
        metrics |= await _metrics(db, ids[i : i + 1000])
    return [StudentRow(s, *metrics.get(s.id, _NO_METRICS)) for s in students]


def filtered_students(
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    program_type_id: uuid.UUID | None = None,
    training_center_id: uuid.UUID | None = None,
    birth_year: int | None = None,
    status: RecordStatus | None = None,
    fee_status: str | None = None,
    sort: str = "fullName",
) -> Select[Any]:
    """Plain filtered student query (no aggregates), ordered; used for counting and paging."""
    stmt = select(Student)
    if search:
        like = contains(search)
        stmt = stmt.where(
            or_(
                Student.full_name.ilike(like),
                Student.student_code.ilike(like),
                Student.parent_name.ilike(like),
                Student.phone.like(like),
                Student.parent_phone.like(like),
            )
        )
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if program_type_id:
        stmt = stmt.where(Student.program_type_id == program_type_id)
    if training_center_id:
        stmt = stmt.where(Student.training_center_id == training_center_id)
    if birth_year:
        start = date(birth_year, 1, 1)  # range, not extract(): lets the date_of_birth index be used
        stmt = stmt.where(Student.date_of_birth >= start, Student.date_of_birth < date(birth_year + 1, 1, 1))
    if status:
        stmt = stmt.where(Student.status == status)
    if fee_status:
        e = FeeLedgerEntry
        stmt = stmt.where(
            select(e.id)
            .where(
                e.student_id == Student.id,
                e.period == month_start(today_local()),
                ledger.status_expr() == fee_status,
            )
            .exists()
        )
    return stmt.order_by(SORTS.get(sort, SORTS["fullName"]), Student.id)


def _list_item(row: StudentRow) -> StudentListItem:
    due, paid, discount = Decimal(row.due), Decimal(row.paid), Decimal(row.discount)
    item = StudentListItem.model_validate(row.student)
    return item.model_copy(
        update={
            "attendance_percentage": float(row.rate) if row.rate is not None else None,
            "current_month_fee_status": row.fee_status,
            "fee_due_to_date": due,
            "paid_to_date": paid,
            "outstanding": max(due - discount - paid, Decimal(0)),
        }
    )


async def list_students(
    db: AsyncSession, params: PageParams, **filters: Any
) -> tuple[list[StudentListItem], int]:
    stmt = filtered_students(**filters)
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    students = list(await db.scalars(stmt.limit(params.page_size).offset(params.offset)))
    return [_list_item(row) for row in await _with_metrics(db, students)], total


async def iter_students(db: AsyncSession, limit: int = 10_000, **filters: Any) -> list[StudentRow]:
    students = list(await db.scalars(filtered_students(**filters).limit(limit)))
    return await _with_metrics(db, students)


async def filter_options(db: AsyncSession) -> FilterOptions:
    years = await db.scalars(
        select(func.distinct(extract("year", Student.date_of_birth)).label("y")).order_by(
            extract("year", Student.date_of_birth).desc()
        )
    )

    async def active(model: type[Category] | type[ProgramType] | type[TrainingCenter]) -> list[RefItem]:
        rows = await db.scalars(
            select(model).where(model.status == RecordStatus.ACTIVE).order_by(model.sort_order, model.name)
        )
        return [RefItem.model_validate(r) for r in rows]

    return FilterOptions(
        birth_years=[int(y) for y in years],
        categories=await active(Category),
        program_types=await active(ProgramType),
        training_centers=await active(TrainingCenter),
    )


# --- detail ----------------------------------------------------------------------------


async def student_detail(db: AsyncSession, student: Student) -> StudentDetail:
    counts = dict(
        (
            await db.execute(
                select(StudentAttendance.status, func.count())
                .where(StudentAttendance.student_id == student.id)
                .group_by(StudentAttendance.status)
            )
        ).all()
    )
    due, discount, paid = (
        await db.execute(
            select(
                func.coalesce(func.sum(FeeLedgerEntry.amount_due), 0),
                func.coalesce(func.sum(FeeLedgerEntry.discount), 0),
                func.coalesce(func.sum(FeeLedgerEntry.amount_paid), 0),
            ).where(FeeLedgerEntry.student_id == student.id)
        )
    ).one()
    row = (await _with_metrics(db, [student]))[0]
    totals = StudentTotals(
        present=counts.get(AttendanceStatus.PRESENT, 0),
        absent=counts.get(AttendanceStatus.ABSENT, 0),
        informed=counts.get(AttendanceStatus.INFORMED, 0),
        fee_due_to_date=Decimal(due),
        paid_to_date=Decimal(paid),
        discount_to_date=Decimal(discount),
        outstanding=max(Decimal(due) - Decimal(discount) - Decimal(paid), Decimal(0)),
    )
    base = _list_item(row).model_dump()
    return StudentDetail.model_validate(_detail_dict(student, base, totals))


def _detail_dict(student: Student, base: dict[str, Any], totals: StudentTotals) -> dict[str, Any]:
    extra = {
        field: getattr(student, field)
        for field in (
            "blood_group",
            "email",
            "address",
            "admission_number",
            "admission_date",
            "remarks",
            "parent_relationship",
            "parent_email",
            "parent_address",
            "emergency_name",
            "emergency_relationship",
            "emergency_phone",
            "created_at",
            "updated_at",
        )
    }
    return {**base, **extra, "totals": totals}


# --- create / update / delete -------------------------------------------------------


async def _check_references(db: AsyncSession, values: dict[str, Any]) -> None:
    for field, model in (
        ("category_id", Category),
        ("program_type_id", ProgramType),
        ("training_center_id", TrainingCenter),
    ):
        if values.get(field) is not None:
            await reference.require_active(db, model, values[field])


async def _check_admission_number(db: AsyncSession, number: str, exclude: uuid.UUID | None = None) -> None:
    stmt = select(Student.id).where(func.lower(Student.admission_number) == number.lower())
    if exclude:
        stmt = stmt.where(Student.id != exclude)
    if await db.scalar(stmt):
        raise Conflict("This admission number is already in use", code="ADMISSION_NUMBER_EXISTS")


async def _check_email(db: AsyncSession, email: str | None, exclude: uuid.UUID | None = None) -> None:
    if not email:
        return
    stmt = select(Student.id).where(Student.email == email)  # citext: case-insensitive
    if exclude:
        stmt = stmt.where(Student.id != exclude)
    if await db.scalar(stmt):
        raise Conflict("Another student already uses this email", code="EMAIL_EXISTS")


async def build_student(db: AsyncSession, data: StudentIn) -> Student:
    """Validate references and create (flush) a student; does not commit."""
    values = data.model_dump()
    await _check_references(db, values)
    photo = await files.get_file_of_purpose(db, values.pop("photo_file_id"), FilePurpose.STUDENT_PHOTO)
    year = data.admission_date.year
    await _check_email(db, values.get("email"))
    if values.get("admission_number"):
        await _check_admission_number(db, values["admission_number"])
    else:
        values["admission_number"] = await next_code(db, "admission", year, "ADM-{year}-{n:03d}")
    student = Student(
        **values,
        student_code=await next_code(db, "student", year, "MSRF-{year}-{n:03d}"),
        photo_file_id=photo.id if photo else None,
    )
    db.add(student)
    await db.flush()
    await ledger.open_first_entry(db, student)
    return student


async def create_student(db: AsyncSession, data: StudentIn, actor: User) -> StudentDetail:
    student = await build_student(db, data)
    await notifications.notify_admins(
        db,
        NotificationType.ADMISSION,
        "New admission registered",
        f"{student.full_name} ({student.student_code}) was added by {actor.full_name}.",
        link=f"/super-admin/students/{student.id}",
        related_id=student.id,
        exclude_user_id=actor.id,
    )
    await db.commit()
    await db.refresh(student)
    return await student_detail(db, student)


async def update_student(db: AsyncSession, student_id: uuid.UUID, data: StudentPatch) -> StudentDetail:
    student = await get_student(db, student_id)
    values = data.model_dump(exclude_unset=True)
    await _check_references(db, values)
    if values.get("admission_number"):
        await _check_admission_number(db, values["admission_number"], exclude=student_id)
    if values.get("email"):
        await _check_email(db, values["email"], exclude=student_id)
    if "photo_file_id" in values:
        photo = await files.get_file_of_purpose(db, values.pop("photo_file_id"), FilePurpose.STUDENT_PHOTO)
        student.photo_file_id = photo.id if photo else None
    reactivated = values.get("status") is RecordStatus.ACTIVE and student.status is RecordStatus.INACTIVE

    required = set(StudentIn.model_fields) - {
        f for f, info in StudentIn.model_fields.items() if not info.is_required()
    }
    for field, value in values.items():
        if value is None and (field in required or field == "status"):
            continue  # required columns cannot be cleared with null
        setattr(student, field, value)
    # A new monthly fee applies from next month's ledger entry; existing entries keep their amount.
    await db.flush()
    if reactivated:
        await ledger.open_first_entry(db, student)
    await db.commit()
    await db.refresh(student)
    return await student_detail(db, student)


async def delete_student(db: AsyncSession, student_id: uuid.UUID) -> None:
    student = await get_student(db, student_id)
    has_history = await db.scalar(
        select(
            select(StudentAttendance.id).where(StudentAttendance.student_id == student_id).exists()
            | select(Payment.id).where(Payment.student_id == student_id).exists()
            | select(PerformanceReport.id).where(PerformanceReport.student_id == student_id).exists()
        )
    )
    if has_history:
        raise Conflict(
            "This student has attendance, payments or reports on record. Deactivate instead.",
            code="STUDENT_HAS_HISTORY",
        )
    for entry in await db.scalars(select(FeeLedgerEntry).where(FeeLedgerEntry.student_id == student_id)):
        await db.delete(entry)  # unpaid generated dues only (no payments exist)
    documents = list(
        await db.scalars(select(StudentDocument).where(StudentDocument.student_id == student_id))
    )
    pending = []
    for doc in documents:
        stored = doc.file
        await db.delete(doc)
        await db.flush()
        pending.append(await files.delete_stored_file(db, stored))
    await db.delete(student)
    await db.commit()
    await files.purge_objects(pending)


# --- documents -----------------------------------------------------------------------


def document_out(doc: StudentDocument) -> StudentDocumentOut:
    return StudentDocumentOut(
        id=doc.id,
        title=doc.title,
        file=doc.file,
        uploaded_at=doc.created_at,
        uploaded_by=doc.uploaded_by.full_name if doc.uploaded_by else None,
    )


async def list_documents(db: AsyncSession, student_id: uuid.UUID) -> list[StudentDocumentOut]:
    await get_student(db, student_id)
    docs = await db.scalars(
        select(StudentDocument)
        .where(StudentDocument.student_id == student_id)
        .order_by(StudentDocument.created_at.desc())
    )
    return [document_out(d) for d in docs]


async def add_document(
    db: AsyncSession, student_id: uuid.UUID, title: str, upload: UploadFile, actor: User
) -> StudentDocumentOut:
    await get_student(db, student_id)
    stored = await files.store_upload(
        db, upload, purpose=FilePurpose.STUDENT_DOCUMENT, uploaded_by_id=actor.id
    )
    doc = StudentDocument(student_id=student_id, file_id=stored.id, title=title, uploaded_by_id=actor.id)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return document_out(doc)


async def delete_document(db: AsyncSession, student_id: uuid.UUID, document_id: uuid.UUID) -> None:
    doc = await db.scalar(
        select(StudentDocument).where(
            StudentDocument.id == document_id, StudentDocument.student_id == student_id
        )
    )
    if doc is None:
        raise NotFound("Document not found", code="DOCUMENT_NOT_FOUND")
    stored = doc.file
    await db.delete(doc)
    await db.flush()
    pending = await files.delete_stored_file(db, stored)
    await db.commit()
    await files.purge_objects([pending])


def export_rows(rows: Iterable[StudentRow]) -> Iterable[dict[str, object]]:
    for s, rate, fee_status, due, paid, discount in rows:
        yield {
            "Student Code": s.student_code,
            "Full Name": s.full_name,
            "Gender": s.gender.value,
            "Date of Birth": s.date_of_birth.isoformat(),
            "Category": s.category.name,
            "Program Type": s.program_type.name,
            "Training Center": s.training_center.name,
            "Batch": s.batch.value,
            "Parent Name": s.parent_name,
            "Parent Phone": s.parent_phone,
            "Phone": s.phone or "",
            "Monthly Fee": s.monthly_fee,
            "Attendance % (90 days)": "" if rate is None else rate,
            "Fee Status (this month)": fee_status or "",
            "Total Fee Due": due,
            "Paid": paid,
            "Discount": discount,
            "Outstanding": max(Decimal(due) - Decimal(discount) - Decimal(paid), Decimal(0)),
            "Status": s.status.value,
        }
