import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentCoach, DbSession, require_admin, require_coach
from app.core.csv_utils import csv_response
from app.core.enums import AttendanceStatus
from app.core.pagination import Page, Pagination, build_page
from app.modules.coaches.service import summarize
from app.modules.sessions import service
from app.modules.sessions.schemas import (
    AttendanceStreamItem,
    AttendanceSummaryItem,
    CoachAttendanceStreamItem,
    RosterOut,
    SessionDetail,
    SessionIn,
    SessionListItem,
)
from app.modules.students.schemas import AttendanceSummaryOut, StudentAttendanceItem, StudentAttendanceOut
from app.modules.students.service import get_student

Year = Annotated[int | None, Query(ge=2020, le=2100)]
Month = Annotated[int | None, Query(ge=1, le=12)]
Search = Annotated[str | None, Query(max_length=100)]
CategoryId = Annotated[uuid.UUID | None, Query(alias="categoryId")]

# --- coach portal ------------------------------------------------------------------------

coach_router = APIRouter(prefix="/coach", tags=["Coach Portal"], dependencies=[Depends(require_coach)])


@coach_router.get(
    "/session-roster", response_model=RosterOut, summary="Active students in the chosen (assigned) categories"
)
async def session_roster(
    coach: CurrentCoach,
    db: DbSession,
    category_ids: Annotated[list[uuid.UUID], Query(alias="categoryIds", min_length=1, max_length=20)],
) -> RosterOut:
    return RosterOut(students=await service.roster(db, coach, category_ids))


@coach_router.get(
    "/sessions", response_model=Page[SessionListItem], summary="My sessions (creator or co-coach)"
)
async def list_my_sessions(
    coach: CurrentCoach,
    db: DbSession,
    pagination: Pagination,
    search: Search = None,
    category_id: CategoryId = None,
    year: Year = None,
    month: Month = None,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> Page[SessionListItem]:
    items, total = await service.list_for_coach(
        db, coach, pagination, search=search, category_id=category_id, year=year, month=month, day=day
    )
    return build_page(items, total, pagination)


@coach_router.post(
    "/sessions",
    response_model=SessionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Log a training session with attendance (one per coach per day)",
)
async def create_session(body: SessionIn, coach: CurrentCoach, db: DbSession) -> SessionDetail:
    return await service.create_session(db, coach, body)


@coach_router.get("/sessions/{session_id}", response_model=SessionDetail, summary="Session detail")
async def get_session(session_id: uuid.UUID, coach: CurrentCoach, db: DbSession) -> SessionDetail:
    session = await service.get_for_coach(db, coach, session_id)
    return await service.session_detail(db, session, coach)


@coach_router.put(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    summary="Replace a session (creator only, within 7 days)",
)
async def update_session(
    session_id: uuid.UUID, body: SessionIn, coach: CurrentCoach, db: DbSession
) -> SessionDetail:
    return await service.update_session(db, coach, session_id, body)


@coach_router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session (creator only, within 7 days)",
)
async def delete_session(session_id: uuid.UUID, coach: CurrentCoach, db: DbSession) -> Response:
    await service.delete_session(db, coach, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- admin -------------------------------------------------------------------------------

admin_router = APIRouter(tags=["Attendance & Sessions"], dependencies=[Depends(require_admin)])


@admin_router.get("/sessions", response_model=Page[SessionListItem], summary="All session reports")
async def list_sessions(
    db: DbSession,
    pagination: Pagination,
    coach_id: Annotated[uuid.UUID | None, Query(alias="coachId")] = None,
    category_id: CategoryId = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    search: Search = None,
) -> Page[SessionListItem]:
    items, total = await service.list_for_admin(
        db,
        pagination,
        coach_id=coach_id,
        category_id=category_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    return build_page(items, total, pagination)


@admin_router.get("/sessions/{session_id}", response_model=SessionDetail, summary="Session report detail")
async def get_session_admin(session_id: uuid.UUID, db: DbSession) -> SessionDetail:
    return await service.get_for_admin(db, session_id)


class StreamFilters:
    def __init__(
        self,
        year: Year = None,
        month: Month = None,
        day: Annotated[date | None, Query(alias="date")] = None,
        category_id: CategoryId = None,
        status_filter: Annotated[AttendanceStatus | None, Query(alias="status")] = None,
        search: Search = None,
    ) -> None:
        service.check_not_future(year, month)
        self.values: dict[str, Any] = {
            "year": year,
            "month": month,
            "day": day,
            "category_id": category_id,
            "status": status_filter,
            "search": search,
        }


class CoachStreamFilters:
    def __init__(
        self,
        year: Year = None,
        month: Month = None,
        day: Annotated[date | None, Query(alias="date")] = None,
        search: Search = None,
    ) -> None:
        service.check_not_future(year, month)
        self.values: dict[str, Any] = {"year": year, "month": month, "day": day, "search": search}


@admin_router.get(
    "/attendance/students", response_model=Page[AttendanceStreamItem], summary="Trainee attendance records"
)
async def student_attendance(
    db: DbSession, pagination: Pagination, filters: Annotated[StreamFilters, Depends()]
) -> Page[AttendanceStreamItem]:
    items, total = await service.attendance_stream(db, pagination, **filters.values)
    return build_page(items, total, pagination)


@admin_router.get(
    "/attendance/students/summary",
    response_model=Page[AttendanceSummaryItem],
    summary="Per-student totals and rate band (GOOD >= 85%, NEEDS_ATTENTION >= 70%, else CRITICAL)",
)
async def student_attendance_summary(
    db: DbSession,
    pagination: Pagination,
    year: Year = None,
    month: Month = None,
    category_id: CategoryId = None,
    search: Search = None,
) -> Page[AttendanceSummaryItem]:
    service.check_not_future(year, month)
    items, total = await service.attendance_summary(
        db, pagination, year=year, month=month, category_id=category_id, search=search
    )
    return build_page(items, total, pagination)


@admin_router.get(
    "/attendance/coaches", response_model=Page[CoachAttendanceStreamItem], summary="Coach attendance records"
)
async def coach_attendance(
    db: DbSession, pagination: Pagination, filters: Annotated[CoachStreamFilters, Depends()]
) -> Page[CoachAttendanceStreamItem]:
    items, total = await service.coach_attendance_stream(db, pagination, **filters.values)
    return build_page(items, total, pagination)


@admin_router.get(
    "/attendance/students/export", response_class=StreamingResponse, summary="Trainee attendance CSV"
)
async def export_student_attendance(
    db: DbSession, filters: Annotated[StreamFilters, Depends()]
) -> StreamingResponse:
    rows = (await db.execute(service.student_attendance_query(**filters.values).limit(50_000))).all()
    header = ["Date", "Student Code", "Student", "Category", "Status", "Remarks", "Marked By"]
    data = (
        {
            "Date": a.session_date.isoformat(),
            "Student Code": st.student_code,
            "Student": st.full_name,
            "Category": st.category.name,
            "Status": a.status.value,
            "Remarks": a.remarks or "",
            "Marked By": s.created_by.user.full_name,
        }
        for a, s, st in rows
    )
    return csv_response("msrf_trainee_attendance", header, data)


@admin_router.get(
    "/attendance/coaches/export", response_class=StreamingResponse, summary="Coach attendance CSV"
)
async def export_coach_attendance(
    db: DbSession, filters: Annotated[CoachStreamFilters, Depends()]
) -> StreamingResponse:
    rows = (await db.execute(service.coach_attendance_query(**filters.values).limit(50_000))).all()
    header = ["Date", "Coach", "Phone", "Venue", "Role", "Status", "Remarks"]
    data = (
        {
            "Date": sc.session_date.isoformat(),
            "Coach": sc.coach.user.full_name,
            "Phone": sc.coach.phone,
            "Venue": s.venue,
            "Role": sc.role.value,
            "Status": sc.status.value,
            "Remarks": sc.remarks or "",
        }
        for sc, s in rows
    )
    return csv_response("msrf_coach_attendance", header, data)


@admin_router.get(
    "/students/{student_id}/attendance", response_model=StudentAttendanceOut, summary="A student's attendance"
)
async def student_attendance_history(
    student_id: uuid.UUID, db: DbSession, year: Year = None, month: Month = None
) -> StudentAttendanceOut:
    await get_student(db, student_id)
    rows = await service.student_attendance_history(db, student_id, year=year, month=month)
    items = [
        StudentAttendanceItem(
            session_id=s.id,
            date=a.session_date,
            status=a.status,
            remarks=a.remarks,
            categories=[c.name for c in s.categories],
            coach_name=s.created_by.user.full_name,
        )
        for a, s in rows
    ]
    summary = summarize([i.status for i in items])
    return StudentAttendanceOut(
        items=items, summary=AttendanceSummaryOut.model_validate(summary.model_dump())
    )
