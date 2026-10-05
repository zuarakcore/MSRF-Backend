import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    verify_password,
)


def _claims(**overrides: object) -> dict[str, object]:
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "role": "ADMIN",
        "ver": 0,
        "type": "access",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def _sign(claims: dict[str, object], key: str | None = None) -> str:
    return jwt.encode(claims, key or get_settings().JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("a-long-password")
    assert hashed.startswith("$argon2id$")
    assert verify_password("a-long-password", hashed)[0] is True
    assert verify_password("wrong-password", hashed)[0] is False


def test_verify_password_without_hash_is_false() -> None:
    assert verify_password("anything", None) == (False, None)


def test_access_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id=user_id, role="COACH", token_version=3)
    claims = decode_access_token(token)
    assert (claims.user_id, claims.role, claims.token_version) == (user_id, "COACH", 3)
    assert expires_in == get_settings().JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_expired_token_rejected_with_specific_code() -> None:
    token = _sign(_claims(exp=datetime.now(UTC) - timedelta(seconds=1)))
    with pytest.raises(Unauthorized) as exc:
        decode_access_token(token)
    assert exc.value.code == "TOKEN_EXPIRED"


@pytest.mark.parametrize(
    "token_factory",
    [
        pytest.param(lambda: _sign(_claims(), key="some-other-secret-key-of-enough-length"), id="wrong-key"),
        pytest.param(lambda: _sign(_claims(aud="another-app")), id="wrong-audience"),
        pytest.param(lambda: _sign(_claims(iss="someone-else")), id="wrong-issuer"),
        pytest.param(lambda: _sign(_claims(type="refresh")), id="wrong-type"),
        pytest.param(lambda: _sign({k: v for k, v in _claims().items() if k != "ver"}), id="missing-ver"),
        pytest.param(lambda: jwt.encode(_claims(), key="", algorithm="none"), id="alg-none"),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_invalid_tokens_rejected(token_factory: object) -> None:
    with pytest.raises(Unauthorized) as exc:
        decode_access_token(token_factory())  # type: ignore[operator]
    assert exc.value.code == "TOKEN_INVALID"


def test_hash_token_is_deterministic_sha256() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64
