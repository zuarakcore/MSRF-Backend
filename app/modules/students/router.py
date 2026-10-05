import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, DbSession, require_admin
from app.core.csv_utils import csv_response
from app.core.enums import RecordStatus
from app.core.pagination import Page, Pagination, build_page
from app.modules.students import csv_io, service
from app.modules.students.schemas import (
    FilterOptions,
    ImportResult,
    StudentDetail,
    StudentDocumentOut,
    StudentIn,
    StudentListItem,
    StudentPatch,
)

router = APIRouter(prefix="/students", tags=["Students"], dependencies=[Depends(require_admin)])

FeeStatusFilter = Literal["PAID", "PENDING", "OVERDUE"]


class StudentFilters:
    """Query parameters shared by the list and the CSV export."""

    def __init__(
        self,
        search: Annotated[str | None, Query(max_length=100)] = None,
        category_id: Annotated[uuid.UUID | None, Query(alias="categoryId")] = None,
        program_type_id: Annotated[uuid.UUID | None, Query(alias="programTypeId")] = None,
        training_center_id: Annotated[uuid.UUID | None, Query(alias="trainingCenterId")] = None,
        birth_year: Annotated[int | None, Query(alias="birthYear", ge=1990, le=2100)] = None,
        status: Annotated[RecordStatus | None, Query()] = None,
        fee_status: Annotated[FeeStatusFilter | None, Query(alias="feeStatus")] = None,
        sort: Annotated[service.SortKey, Query()] = "fullName",
    ) -> None:
        self.values: dict[str, Any] = {
            "search": search,
            "category_id": category_id,
            "program_type_id": program_type_id,
            "training_center_id": training_center_id,
            "birth_year": birth_year,
            "status": status,
            "fee_status": fee_status,
            "sort": sort,
        }


Filters = Annotated[StudentFilters, Depends()]


@router.get("", response_model=Page[StudentListItem], summary="Student roster with filters")
async def list_students(db: DbSession, pagination: Pagination, filters: Filters) -> Page[StudentListItem]:
    items, total = await service.list_students(db, pagination, **filters.values)
    return build_page(items, total, pagination)


@router.get("/filter-options", response_model=FilterOptions, summary="Values for the roster filter dropdowns")
async def filter_options(db: DbSession) -> FilterOptions:
    return await service.filter_options(db)


@router.get("/export", response_class=StreamingResponse, summary="CSV of the full filtered roster")
async def export_students(db: DbSession, filters: Filters) -> StreamingResponse:
    rows = await service.iter_students(db, **filters.values)
    data = list(service.export_rows(rows))
    header = list(data[0].keys()) if data else ["Student Code", "Full Name"]
    return csv_response("msrf_students", header, data)


@router.get("/import-template", response_class=StreamingResponse, summary="CSV template for bulk import")
async def import_template() -> StreamingResponse:
    return csv_response("msrf_student_import_template", list(csv_io.COLUMNS), csv_io.template_rows())


@router.post(
    "/import",
    response_model=ImportResult,
    summary="Bulk import from CSV (all rows or none; use dryRun=true to validate only)",
)
async def import_students(
    db: DbSession,
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Query(alias="dryRun")] = False,
) -> ImportResult:
    created = await csv_io.import_students(db, file, dry_run=dry_run)
    return ImportResult(created=created, dry_run=dry_run)


@router.post(
    "", response_model=StudentDetail, status_code=status.HTTP_201_CREATED, summary="Register an admission"
)
async def create_student(body: StudentIn, db: DbSession, admin: AdminUser) -> StudentDetail:
    return await service.create_student(db, body, admin)


@router.get("/{student_id}", response_model=StudentDetail, summary="Student profile")
async def get_student(student_id: uuid.UUID, db: DbSession) -> StudentDetail:
    return await service.student_detail(db, await service.get_student(db, student_id))


@router.patch("/{student_id}", response_model=StudentDetail, summary="Update a student (including status)")
async def update_student(student_id: uuid.UUID, body: StudentPatch, db: DbSession) -> StudentDetail:
    return await service.update_student(db, student_id, body)


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a student without history (409 otherwise; deactivate instead)",
)
async def delete_student(student_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_student(db, student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{student_id}/documents", response_model=list[StudentDocumentOut], summary="Student documents")
async def list_documents(student_id: uuid.UUID, db: DbSession) -> list[StudentDocumentOut]:
    return await service.list_documents(db, student_id)


@router.post(
    "/{student_id}/documents",
    response_model=StudentDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (PDF, DOC, DOCX, JPEG, PNG; max 10 MB)",
)
async def add_document(
    student_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
    title: Annotated[str, Form(min_length=1, max_length=120)],
    file: Annotated[UploadFile, File()],
) -> StudentDocumentOut:
    return await service.add_document(db, student_id, title.strip(), file, admin)


@router.delete(
    "/{student_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(student_id: uuid.UUID, document_id: uuid.UUID, db: DbSession) -> Response:
    await service.delete_document(db, student_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
