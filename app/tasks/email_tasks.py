import logging
import smtplib

from app.core.email import OutgoingEmail, deliver
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="email.send",
    autoretry_for=(smtplib.SMTPException, OSError),
    retry_backoff=30,  # 30s, 60s, 120s, ...
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def send_email(self: object, message: dict[str, str]) -> None:
    deliver(OutgoingEmail(**message))
    logger.info(
        "email sent", extra={"to_domain": message["to"].rsplit("@", 1)[-1], "subject": message["subject"]}
    )
