from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from app.core import email as email_module
from app.core.config import get_settings


class _FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None: ...
    def starttls(self, **kwargs: Any) -> None: ...
    def login(self, *args: Any) -> None: ...
    def send_message(self, message: EmailMessage) -> None:
        _FakeSMTP.sent.append(message)


def test_emails_embed_the_logo_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(email_module.smtplib, "SMTP", _FakeSMTP)
    message = email_module.render_email(
        "password_reset",
        "coach@example.com",
        {"full_name": "Coach", "link": "http://x", "expires_minutes": 30},
    )
    assert "cid:msrf-logo" in message.html

    email_module.deliver(message)

    sent = _FakeSMTP.sent[-1]
    images = [p for p in sent.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1
    assert images[0]["Content-ID"] == "<msrf-logo>"
    assert sent.get_body(("plain",)) is not None  # text version still present for plain-text clients
