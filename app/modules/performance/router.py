import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentCoach, CurrentUser, DbSession, require_admin, require_coach
from app.core.pagination import Page, Pagination, build_page
from app.modules.performance import service
from app.modules.performance.schemas import (
    PerformanceConfig,
    PerformanceReportDetail,
    PerformanceReportIn,
    PerformanceReportSummary,
)
from app.modules.students.service import get_student

Search = Annotated[str | None, Query(max_length=100)]

config_router = APIRouter(tags=["Performance Reports"])


@config_router.get(
    "/performance-reports/config",
    response_model=PerformanceConfig,
    summary="The 15 skills, standard goals and positions used by the report form",
)
async def performance_config(_: CurrentUser) -> PerformanceConfig:
    return service.config()


coach_router = APIRouter(prefix="/coach", tags=["Coach Portal"], dependencies=[Depends(require_coach)])


@coach_router.get(
    "/performance-reports",
    response_model=Page[PerformanceReportSummary],
    summary="My player development reports",
)
async def list_my_reports(
    coach: CurrentCoach,
    db: DbSession,
    pagination: Pagination,
    search: Search = None,
    student_id: Annotated[uuid.UUID | None, Query(alias="studentId")] = None,
) -> Page[PerformanceReportSummary]:
    items, total = await service.list_reports(
        db, pagination, viewer=coach, coach_id=coach.id, search=search, student_id=student_id
    )
    return build_page(items, total, pagination)


@coach_router.post(
    "/performance-reports",
    response_model=PerformanceReportDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a 15-skill player development report",
)
async def create_report(
    body: PerformanceReportIn, coach: CurrentCoach, db: DbSession
) -> PerformanceReportDetail:
    return await service.create_report(db, coach, body)


@coach_router.get(
    "/performance-reports/{report_id}", response_model=PerformanceReportDetail, summary="Report"
)
async def get_my_report(report_id: uuid.UUID, coach: CurrentCoach, db: DbSession) -> PerformanceReportDetail:
    return service.detail(await service.get_for_coach(db, coach, report_id), coach)


@coach_router.put(
    "/performance-reports/{report_id}",
    response_model=PerformanceReportDetail,
    summary="Replace a report (author only, within 7 days)",
)
async def update_report(
    report_id: uuid.UUID, body: PerformanceReportIn, coach: CurrentCoach, db: DbSession
) -> PerformanceReportDetail:
    return await service.update_report(db, coach, report_id, body)


@coach_router.delete(
    "/performance-reports/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a report (author only, within 7 days)",
)
async def delete_report(report_id: uuid.UUID, coach: CurrentCoach, db: DbSession) -> Response:
    await service.delete_report(db, coach, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


admin_router = APIRouter(tags=["Performance Reports"], dependencies=[Depends(require_admin)])


@admin_router.get(
    "/performance-reports",
    response_model=Page[PerformanceReportSummary],
    summary="All player development reports",
)
async def list_reports(
    db: DbSession,
    pagination: Pagination,
    coach_id: Annotated[uuid.UUID | None, Query(alias="coachId")] = None,
    student_id: Annotated[uuid.UUID | None, Query(alias="studentId")] = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    search: Search = None,
) -> Page[PerformanceReportSummary]:
    items, total = await service.list_reports(
        db,
        pagination,
        coach_id=coach_id,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    return build_page(items, total, pagination)


@admin_router.get(
    "/performance-reports/{report_id}", response_model=PerformanceReportDetail, summary="Report detail"
)
async def get_report(report_id: uuid.UUID, db: DbSession) -> PerformanceReportDetail:
    return service.detail(await service.get_report(db, report_id))


@admin_router.get(
    "/students/{student_id}/performance-reports",
    response_model=list[PerformanceReportSummary],
    summary="A student's reports",
)
async def student_reports(student_id: uuid.UUID, db: DbSession) -> list[PerformanceReportSummary]:
    await get_student(db, student_id)
    return await service.reports_for_student(db, student_id)
