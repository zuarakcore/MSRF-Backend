import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.redis import get_redis

router = APIRouter(prefix="/health", tags=["Health"])
logger = logging.getLogger(__name__)


@router.get("/live", summary="Process is running")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Database and Redis are reachable", response_model=None)
async def ready(db: DbSession) -> dict[str, str] | JSONResponse:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        logger.error("Readiness: database unavailable", extra={"error": str(exc)})
        checks["db"] = "unavailable"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("Readiness: redis unavailable", extra={"error": str(exc)})
        checks["redis"] = "unavailable"

    if all(v == "ok" for v in checks.values()):
        return {"status": "ok", **checks}
    return JSONResponse({"status": "unavailable", **checks}, status_code=503)
