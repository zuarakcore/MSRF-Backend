"""Password hashing, access-token JWTs and opaque one-time tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.timeutils import utcnow

# Argon2id with pwdlib's recommended parameters.
_password_hash = PasswordHash.recommended()

# Verified against when the user does not exist, so a login for an unknown email
# takes as long as one for a known email (no account enumeration by timing).
_DUMMY_HASH = _password_hash.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    """Return (is_valid, new_hash_if_parameters_changed)."""
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False, None
    return _password_hash.verify_and_update(password, password_hash)


def burn_password_check(password: str) -> None:
    _password_hash.verify(password, _DUMMY_HASH)


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: str
    token_version: int


def create_access_token(*, user_id: uuid.UUID, role: str, token_version: int) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    settings = get_settings()
    now = utcnow()
    expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {
        "sub": str(user_id),
        "role": role,
        "ver": token_version,
        "type": "access",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> AccessTokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],  # pinned: rejects alg=none and algorithm swaps
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "type", "ver"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token", code="TOKEN_INVALID") from exc

    if payload.get("type") != "access":
        raise Unauthorized("Invalid access token", code="TOKEN_INVALID")
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]), role=str(payload["role"]), token_version=int(payload["ver"])
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Invalid access token", code="TOKEN_INVALID") from exc


def generate_opaque_token() -> str:
    """256-bit random token for refresh, reset and invite links."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored as SHA-256 digests; a database leak does not reveal usable tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
