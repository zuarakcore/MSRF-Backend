"""Cloudflare Turnstile verification for public forms. Disabled when no secret is configured."""

import logging

import httpx

from app.core.config import get_settings
from app.core.errors import BadRequest

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_captcha(token: str | None, remote_ip: str | None) -> None:
    secret = get_settings().TURNSTILE_SECRET_KEY.get_secret_value()
    if not secret:
        return
    if not token:
        raise BadRequest("Please complete the verification challenge", code="CAPTCHA_FAILED")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                VERIFY_URL, data={"secret": secret, "response": token, "remoteip": remote_ip or ""}
            )
        ok = bool(response.json().get("success"))
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Turnstile verification unavailable", extra={"error": str(exc)})
        ok = False
    if not ok:
        raise BadRequest("Verification failed. Please try again.", code="CAPTCHA_FAILED")
