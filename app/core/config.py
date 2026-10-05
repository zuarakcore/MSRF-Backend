"""Application settings.

Django equivalent: settings.py. Differences: values are typed and validated at startup,
read from environment variables (and `.env` in development), and accessed through
`get_settings()` instead of a module-level global.
"""

import re
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PLACEHOLDER_SECRET = "change-me"  # noqa: S105 - the value production startup refuses


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: Environment = Environment.DEVELOPMENT
    DEBUG: bool = False
    APP_NAME: str = "MSRF API"
    API_PREFIX: str = "/api/v1"
    DOCS_ENABLED: bool = True
    TIMEZONE: str = "Asia/Kolkata"

    DATABASE_URL: str = "postgresql+asyncpg://msrf:msrf@localhost:5432/msrf"
    DATABASE_URL_SYNC: str = "postgresql+psycopg://msrf:msrf@localhost:5432/msrf"
    DATABASE_ECHO: bool = False

    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_TASK_ALWAYS_EAGER: bool = False

    JWT_SECRET_KEY: SecretStr = SecretStr(PLACEHOLDER_SECRET)
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=1, le=60)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)
    JWT_REFRESH_ABSOLUTE_EXPIRE_DAYS: int = Field(default=30, ge=1, le=90)
    JWT_ISSUER: str = "msrf-api"
    JWT_AUDIENCE: str = "msrf-admin"
    REFRESH_COOKIE_NAME: str = "msrf_refresh"
    # Two tabs refreshing at once would otherwise look like token theft.
    REFRESH_REUSE_GRACE_SECONDS: int = Field(default=30, ge=0, le=120)

    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    INVITE_EXPIRE_HOURS: int = 72

    # NoDecode: read the raw comma-separated string instead of expecting JSON.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://localhost:3000"]
    # Development convenience: also allow any origin matching this regex (e.g. any localhost port or
    # private-LAN address). Must be empty in production, where only the exact CORS_ORIGINS list applies.
    CORS_ORIGIN_REGEX: str = ""
    TRUSTED_HOSTS: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "test"]
    ADMIN_APP_URL: str = "http://localhost:5173"

    RATE_LIMIT_ENABLED: bool = True
    # Django-admin-style back office at /admin (SQLAdmin). ADMIN accounts only.
    ADMIN_PANEL_ENABLED: bool = True
    # Outer cap on any request body (largest upload is 10 MB plus multipart overhead).
    MAX_REQUEST_BYTES: int = 12 * 1024 * 1024

    # Public base URL of this API (used to build signed file links).
    API_BASE_URL: str = "http://localhost:8000"
    # local: files on disk under MEDIA_ROOT (development). s3: any S3-compatible store.
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    MEDIA_ROOT: str = "media"
    # Base URL that serves PUBLIC files (CDN/bucket URL in production).
    PUBLIC_MEDIA_BASE_URL: str = "http://localhost:8000/media/public"
    # Image returned wherever a record has no photo. Empty = the built-in /static/default-image.png.
    DEFAULT_IMAGE_URL: str = ""
    # Lifetime of signed links to private files (student photos, documents, CVs).
    FILE_URL_TTL_SECONDS: int = Field(default=3600, ge=60, le=86_400)
    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str = "auto"
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    S3_BUCKET_PRIVATE: str = "msrf-private"
    S3_BUCKET_PUBLIC: str = "msrf-public"

    # Cloudflare Turnstile secret for public forms. Empty disables the check.
    TURNSTILE_SECRET_KEY: SecretStr = SecretStr("")

    EMAIL_BACKEND: Literal["smtp", "console", "memory"] = "console"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_USE_TLS: bool = False  # STARTTLS
    SMTP_USE_SSL: bool = False  # implicit TLS (port 465)
    SMTP_TIMEOUT_SECONDS: int = 15
    SMTP_FROM_EMAIL: str = "no-reply@example.com"
    SMTP_FROM_NAME: str = "MSRF Academy"

    @field_validator("CORS_ORIGINS", "TRUSTED_HOSTS", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("TRUSTED_HOSTS", mode="after")
    @classmethod
    def _lowercase_hosts(cls, value: list[str]) -> list[str]:
        # Host names are case-insensitive and browsers send them lowercased.
        return [host.lower() for host in value]

    @property
    def default_image_url(self) -> str:
        return self.DEFAULT_IMAGE_URL or f"{self.API_BASE_URL.rstrip('/')}/static/default-image.png"

    def origin_allowed(self, origin: str) -> bool:
        if origin in self.CORS_ORIGINS:
            return True
        return bool(self.CORS_ORIGIN_REGEX) and re.fullmatch(self.CORS_ORIGIN_REGEX, origin) is not None

    @property
    def is_production(self) -> bool:
        return self.APP_ENV is Environment.PRODUCTION

    @model_validator(mode="after")
    def _guard_production(self) -> Self:
        """Refuse to start production with unsafe settings."""
        if not self.is_production:
            return self
        problems: list[str] = []
        secret = self.JWT_SECRET_KEY.get_secret_value()
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if secret == PLACEHOLDER_SECRET or len(secret) < 32:
            problems.append("JWT_SECRET_KEY must be a random value of at least 32 characters")
        if any(o == "*" or o.startswith("http://") for o in self.CORS_ORIGINS):
            problems.append("CORS_ORIGINS must list explicit https:// origins")
        if self.CORS_ORIGIN_REGEX:
            problems.append("CORS_ORIGIN_REGEX must be empty")
        if not self.TRUSTED_HOSTS or "*" in self.TRUSTED_HOSTS:
            problems.append("TRUSTED_HOSTS must list explicit host names")
        if self.EMAIL_BACKEND != "smtp":
            problems.append("EMAIL_BACKEND must be smtp")
        if self.CELERY_TASK_ALWAYS_EAGER:
            problems.append("CELERY_TASK_ALWAYS_EAGER must be false")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
