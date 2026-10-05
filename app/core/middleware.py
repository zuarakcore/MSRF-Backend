"""HTTP middleware: request IDs, access logging, security headers.

Django equivalent: MIDDLEWARE. Authentication is deliberately *not* middleware here;
it is a dependency on each router, so it is visible in route signatures and tests.
"""

import logging
import re
import time
import uuid

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

logger = logging.getLogger("app.request")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
]
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class _BodyTooLarge(HTTPException):
    """An HTTPException so FastAPI's body parsing re-raises it instead of turning it into a 400."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large")


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are read.

    Starlette spools multipart *files* to disk with no size cap before a route runs, so without
    this an anonymous client could fill the disk through the public upload forms. Per-purpose
    limits (5-10 MB) are still enforced by the upload service; this is the outer guard.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _send_413(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _send_413(send)


async def _send_413(send: Send) -> None:
    body = b'{"detail":"Request body is too large","code":"FILE_TOO_LARGE"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Pure ASGI middleware (no BaseHTTPMiddleware overhead or streaming pitfalls)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        hsts: bool,
        docs_paths: tuple[str, ...],
        embeddable_paths: tuple[str, ...] = (),
        allow_private_network: bool = False,
    ) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_paths = docs_paths
        # Signed file links and public media are meant to be shown in <img> tags on other origins
        # (website, admin app, CDN); access to them is controlled by the URL signature, not CORP.
        self.embeddable_paths = embeddable_paths
        self.allow_private_network = allow_private_network

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500
        path: str = scope["path"]
        request_headers = dict(scope["headers"])
        origin = request_headers.get(b"origin", b"").decode("latin-1")[:200] or None
        wants_private_network = request_headers.get(b"access-control-request-private-network") == b"true"

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.extend(_SECURITY_HEADERS)
                corp = b"cross-origin" if path.startswith(self.embeddable_paths) else b"same-site"
                headers.append((b"cross-origin-resource-policy", corp))
                if self.allow_private_network and wants_private_network:
                    # Chrome's Private/Local Network Access: a page calling a LAN address (e.g. a
                    # developer's laptop calling this Mac) needs this opt-in on the preflight.
                    headers.append((b"access-control-allow-private-network", b"true"))
                if not path.startswith(self.docs_paths):
                    headers.append(_API_CSP)
                if self.hsts:
                    headers.append(_HSTS)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status_code,
                    "origin": origin,
                    "client": (scope.get("client") or ("?",))[0],
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id_var.reset(token)
