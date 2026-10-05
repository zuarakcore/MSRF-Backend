"""Version 1 API: every module router is included here (Django equivalent: urls.py)."""

from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.coach_portal.router import router as coach_portal_router
from app.modules.coaches.router import router as coaches_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.fees.router import router as fees_router
from app.modules.files.router import router as files_router
from app.modules.health.router import router as health_router
from app.modules.notifications.router import router as notifications_router
from app.modules.payment_submissions.router import admin_router as submissions_admin_router
from app.modules.payment_submissions.router import public_router as submissions_public_router
from app.modules.performance.router import admin_router as performance_admin_router
from app.modules.performance.router import coach_router as performance_coach_router
from app.modules.performance.router import config_router as performance_config_router
from app.modules.reference.router import categories_router, program_types_router, training_centers_router
from app.modules.sessions.router import admin_router as sessions_admin_router
from app.modules.sessions.router import coach_router as sessions_coach_router
from app.modules.students.router import router as students_router
from app.modules.websocket.router import router as websocket_router
from app.modules.website.router import admin as website_admin_router
from app.modules.website.router import public as website_public_router

api_router = APIRouter()
for router in (
    health_router,
    auth_router,
    files_router,
    categories_router,
    program_types_router,
    training_centers_router,
    coaches_router,
    students_router,
    sessions_admin_router,
    performance_config_router,  # before the admin router: /performance-reports/config is not an id
    performance_admin_router,
    fees_router,
    submissions_admin_router,
    website_admin_router,
    dashboard_router,
    notifications_router,
    coach_portal_router,
    sessions_coach_router,
    performance_coach_router,
    website_public_router,
    submissions_public_router,
    websocket_router,
):
    api_router.include_router(router)
