import pytest
from pydantic import ValidationError

from app.core.config import Settings

SAFE_PRODUCTION = {
    "APP_ENV": "production",
    "DEBUG": False,
    "JWT_SECRET_KEY": "x" * 48,
    "CORS_ORIGINS": "https://admin.example.com,https://www.example.com",
    "TRUSTED_HOSTS": "api.example.com",
    "EMAIL_BACKEND": "smtp",
    "CELERY_TASK_ALWAYS_EAGER": False,
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PRODUCTION, **overrides})  # type: ignore[arg-type]


def test_safe_production_settings_load() -> None:
    settings = _settings()
    assert settings.CORS_ORIGINS == ["https://admin.example.com", "https://www.example.com"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"DEBUG": True}, "DEBUG"),
        ({"JWT_SECRET_KEY": "change-me"}, "JWT_SECRET_KEY"),
        ({"JWT_SECRET_KEY": "short"}, "JWT_SECRET_KEY"),
        ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "http://admin.example.com"}, "CORS_ORIGINS"),
        ({"EMAIL_BACKEND": "console"}, "EMAIL_BACKEND"),
        ({"CORS_ORIGIN_REGEX": ".*"}, "CORS_ORIGIN_REGEX"),
    ],
)
def test_unsafe_production_settings_refused(override: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**override)
