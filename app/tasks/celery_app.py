"""Celery application.

Run a worker:  celery -A app.tasks.celery_app worker -l info
Run beat:      celery -A app.tasks.celery_app beat -l info   (exactly one instance)
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "msrf", broker=settings.CELERY_BROKER_URL, include=["app.tasks.email_tasks", "app.tasks.scheduled_tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],  # never pickle
    result_backend=None,  # results are not used; tasks report via logs and the DB
    task_ignore_result=True,
    task_acks_late=True,  # a task lost mid-run (worker crash) is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Threads, not the default prefork pool: prefork crashes on macOS with Python 3.12
    # ("not enough values to unpack" in fast_trace_task) and our tasks are I/O-bound (SMTP, DB).
    worker_pool="threads",
    worker_concurrency=4,
    task_time_limit=60,
    task_soft_time_limit=50,
    timezone=settings.TIMEZONE,
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=True,
)

# Times are Asia/Kolkata (settings.TIMEZONE).
celery_app.conf.beat_schedule = {
    # Daily rather than only on the 1st: idempotent, and it self-heals if beat was down on the 1st.
    "generate-monthly-ledger": {
        "task": "fees.generate_monthly_ledger",
        "schedule": crontab(hour=0, minute=5),
    },
    "overdue-digest": {
        "task": "fees.overdue_digest",
        "schedule": crontab(day_of_month="11", hour=9, minute=0),
    },
    "daily-maintenance": {"task": "maintenance.daily", "schedule": crontab(hour=3, minute=0)},
}
