"""Celery wrappers around app.tasks.jobs (scheduled by beat; see celery_app.beat_schedule)."""

from app.core.db_sync import sync_session
from app.tasks import jobs
from app.tasks.celery_app import celery_app


@celery_app.task(
    name="fees.generate_monthly_ledger", autoretry_for=(Exception,), retry_backoff=60, max_retries=5
)
def generate_monthly_ledger() -> int:
    with sync_session() as db:
        return jobs.generate_ledger(db)


@celery_app.task(name="fees.overdue_digest")
def overdue_digest() -> int:
    with sync_session() as db:
        return jobs.overdue_digest(db)


@celery_app.task(name="maintenance.daily")
def daily_maintenance() -> dict[str, int]:
    with sync_session() as db:
        return {
            "orphan_uploads": jobs.purge_orphan_uploads(db),
            "expired_rows": jobs.purge_expired_tokens(db),
            "ledger_drift": jobs.check_ledger_consistency(db),
        }
