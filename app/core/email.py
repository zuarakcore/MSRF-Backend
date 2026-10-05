"""Email rendering and delivery.

Routes and services never talk to SMTP. They call `queue_email(...)` after committing,
which hands the message to a Celery task (retries, does not block the request).
"""

import logging
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "email-logo.png"
LOGO_CID = "msrf-logo"

# Messages "sent" with EMAIL_BACKEND=memory (tests).
outbox: list["OutgoingEmail"] = []


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,  # a missing variable is a bug, not a blank in a customer email
    )


def render_email(template: str, to: str, context: dict[str, Any]) -> OutgoingEmail:
    """Render `<template>.subject.txt`, `<template>.txt` and `<template>.html`."""
    ctx = {"app_name": get_settings().SMTP_FROM_NAME, **context}
    env = _env()
    subject = env.get_template(f"{template}.subject.txt").render(ctx).strip()
    text = env.get_template(f"{template}.txt").render(ctx)
    html = env.get_template(f"{template}.html").render(ctx)
    return OutgoingEmail(to=to, subject=subject, text=text, html=html)


def deliver(message: OutgoingEmail) -> None:
    settings = get_settings()
    if settings.EMAIL_BACKEND == "memory":
        outbox.append(message)
        return
    if settings.EMAIL_BACKEND == "console":
        logger.info("email (console backend)", extra={"to": message.to, "subject": message.subject})
        print(f"--- email to {message.to}: {message.subject}\n{message.text}\n---")
        return

    mime = EmailMessage()
    mime["From"] = formataddr((settings.SMTP_FROM_NAME, settings.SMTP_FROM_EMAIL))
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Message-ID"] = make_msgid(domain=settings.SMTP_FROM_EMAIL.rsplit("@", 1)[-1])
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    if f"cid:{LOGO_CID}" in message.html and LOGO_PATH.exists():
        # Inline (related) image: email clients show it without loading anything from our server.
        for part in mime.iter_parts():
            if part.get_content_type() == "text/html":
                part.add_related(LOGO_PATH.read_bytes(), maintype="image", subtype="png", cid=f"<{LOGO_CID}>")

    context = ssl.create_default_context()
    smtp_cls = smtplib.SMTP_SSL if settings.SMTP_USE_SSL else smtplib.SMTP
    kwargs: dict[str, Any] = {"timeout": settings.SMTP_TIMEOUT_SECONDS}
    if settings.SMTP_USE_SSL:
        kwargs["context"] = context
    with smtp_cls(settings.SMTP_HOST, settings.SMTP_PORT, **kwargs) as smtp:
        if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
            smtp.starttls(context=context)
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value())
        smtp.send_message(mime)


def queue_email(template: str, to: str, context: dict[str, Any]) -> None:
    """Render now (fails fast on template errors) and send in the Celery worker.

    Call only after the database transaction has committed, so the worker never
    sends an email for data that was rolled back.
    """
    from app.tasks.email_tasks import send_email  # local import: avoid core -> tasks cycle at import

    message = render_email(template, to, context)
    send_email.delay(message.to_dict())
