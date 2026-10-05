"""Synchronous engine for Celery tasks (Celery workers are not async)."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_sync_engine() -> Engine:
    return create_engine(get_settings().DATABASE_URL_SYNC, pool_pre_ping=True, pool_size=5)


@contextmanager
def sync_session() -> Iterator[Session]:
    with sessionmaker(get_sync_engine(), expire_on_commit=False)() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
