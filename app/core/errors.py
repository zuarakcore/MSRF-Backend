"""Application errors and the exception handlers that turn them into JSON.

Every error response has the shape {"detail": str, "code": str}, plus "errors" for
validation failures. Services raise AppError subclasses; they never build responses.
"""

import logging
from typing import Any, ClassVar

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic.alias_generators import to_camel
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code: ClassVar[int] = status.HTTP_400_BAD_REQUEST
    default_code: ClassVar[str] = "BAD_REQUEST"
    default_detail: ClassVar[str] = "Bad request"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.code = code or self.default_code
        self.headers = headers
        self.errors = errors
        super().__init__(self.detail)


class BadRequest(AppError):
    pass


class Unauthorized(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "NOT_AUTHENTICATED"
    default_detail = "Authentication required"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        super().__init__(detail, code=code, headers={"WWW-Authenticate": "Bearer"})


class Forbidden(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "FORBIDDEN"
    default_detail = "You do not have permission to perform this action"


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"
    default_detail = "Not found"


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CONFLICT"
    default_detail = "Conflict with the current state of the resource"


class BusinessRuleViolation(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    default_code = "BUSINESS_RULE_VIOLATION"
    default_detail = "The request breaks a business rule"


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "RATE_LIMITED"
    default_detail = "Too many requests. Please try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__(headers={"Retry-After": str(max(retry_after, 1))})


class ServiceUnavailable(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "SERVICE_UNAVAILABLE"
    default_detail = "Service temporarily unavailable"


# Unique/check constraint name -> (status, code, detail). Catches races that
# service-level pre-checks cannot (two requests inserting the same email at once).
INTEGRITY_ERROR_MAP: dict[str, tuple[int, str, str]] = {
    "uq_users_email": (409, "EMAIL_EXISTS", "A user with this email already exists"),
}


def _error_body(detail: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"detail": detail, "code": code, **extra}


def _field_path(loc: tuple[int | str, ...]) -> str:
    parts = [p for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    return ".".join(to_camel(p) if isinstance(p, str) else str(p) for p in parts)


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    extra = {"errors": exc.errors} if exc.errors else {}
    return JSONResponse(
        _error_body(exc.detail, exc.code, **extra), status_code=exc.status_code, headers=exc.headers
    )


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"field": _field_path(tuple(err.get("loc", ()))), "message": str(err.get("msg", "Invalid value"))}
        for err in exc.errors()
    ]
    return JSONResponse(
        _error_body("Validation error", "VALIDATION_ERROR", errors=errors),
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "NOT_AUTHENTICATED", 413: "FILE_TOO_LARGE"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    return JSONResponse(
        _error_body(str(exc.detail), code),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def integrity_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IntegrityError)
    constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None) or getattr(
        exc.orig, "constraint_name", None
    )
    sqlstate = getattr(getattr(exc.orig, "__cause__", None), "sqlstate", None) or getattr(
        exc.orig, "sqlstate", None
    )
    if constraint in INTEGRITY_ERROR_MAP:
        status_code, code, detail = INTEGRITY_ERROR_MAP[constraint]
        return JSONResponse(_error_body(detail, code), status_code=status_code)
    if constraint and constraint.endswith("_name_lower"):
        return JSONResponse(_error_body("This name already exists", "NAME_EXISTS"), status_code=409)
    if sqlstate == "23503":  # foreign_key_violation: a RESTRICT reference blocks a delete
        return JSONResponse(
            _error_body("This record is in use by other records. Deactivate it instead.", "IN_USE"),
            status_code=status.HTTP_409_CONFLICT,
        )
    logger.exception("Unmapped integrity error", extra={"constraint": constraint})
    return JSONResponse(
        _error_body("The request conflicts with existing data", "CONFLICT"),
        status_code=status.HTTP_409_CONFLICT,
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        _error_body("Internal server error", "INTERNAL_ERROR", requestId=request_id_var.get()),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
