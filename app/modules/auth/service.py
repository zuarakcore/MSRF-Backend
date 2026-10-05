"""Authentication business logic. No HTTP here: routers handle cookies and status codes."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.email import queue_email
from app.core.errors import BadRequest, BusinessRuleViolation, Forbidden, Unauthorized
from app.core.security import (
    burn_password_check,
    create_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core.timeutils import utcnow
from app.modules.audit import service as audit
from app.modules.auth.models import AuthToken, AuthTokenPurpose, RefreshToken
from app.modules.users.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    user: User
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_max_age: int  # seconds, for the cookie


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip_address: str | None
    user_agent: str | None


def _new_refresh_row(
    user: User,
    client: ClientInfo,
    *,
    family_id: uuid.UUID | None = None,
    absolute_expires_at: datetime | None = None,
) -> tuple[RefreshToken, str]:
    settings = get_settings()
    now = utcnow()
    raw = generate_opaque_token()
    absolute = absolute_expires_at or now + timedelta(days=settings.JWT_REFRESH_ABSOLUTE_EXPIRE_DAYS)
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        expires_at=min(now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS), absolute),
        absolute_expires_at=absolute,
        user_agent=(client.user_agent or "")[:300] or None,
        ip_address=client.ip_address,
    )
    return row, raw


def _issue(user: User, row: RefreshToken, raw_refresh: str) -> IssuedTokens:
    access, expires_in = create_access_token(
        user_id=user.id, role=user.role.value, token_version=user.token_version
    )
    max_age = int((row.expires_at - utcnow()).total_seconds())
    return IssuedTokens(user, access, expires_in, raw_refresh, max_age)


async def _revoke_all_refresh_tokens(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def _check_password_policy(password: str, email: str) -> None:
    if password.strip().lower() == email.strip().lower():
        raise BusinessRuleViolation("Password must not be your email address", code="WEAK_PASSWORD")


async def login(db: AsyncSession, email: str, password: str, client: ClientInfo) -> IssuedTokens:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check(password)  # equal timing for unknown emails
        raise Unauthorized("Incorrect email or password", code="INVALID_CREDENTIALS")

    valid, new_hash = verify_password(password, user.password_hash)
    if not valid:
        audit.record(db, "LOGIN_FAILED", actor_user_id=user.id, ip_address=client.ip_address)
        await db.commit()
        raise Unauthorized("Incorrect email or password", code="INVALID_CREDENTIALS")
    # Reported only after a correct password, so it does not reveal which accounts exist.
    if not user.is_active:
        raise Forbidden("This account is inactive", code="ACCOUNT_INACTIVE")

    if new_hash:
        user.password_hash = new_hash
    user.last_login_at = utcnow()
    row, raw = _new_refresh_row(user, client)
    db.add(row)
    await db.commit()
    return _issue(user, row, raw)


async def refresh(db: AsyncSession, raw_token: str | None, client: ClientInfo) -> IssuedTokens:
    invalid = Unauthorized("Session expired. Please sign in again.", code="INVALID_REFRESH_TOKEN")
    if not raw_token:
        raise invalid

    # FOR UPDATE: two concurrent refreshes of the same token are serialised.
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)).with_for_update()
    )
    if row is None or (row.revoked_at is not None and row.replaced_at is None):
        raise invalid

    now = utcnow()
    settings = get_settings()
    if row.replaced_at is not None:
        within_grace = now - row.replaced_at <= timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
        if not within_grace:
            # A rotated token came back: assume it was stolen. Kill the whole login.
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            audit.record(db, "REFRESH_TOKEN_REUSED", actor_user_id=row.user_id, ip_address=client.ip_address)
            await db.commit()
            logger.warning("Refresh token reuse detected", extra={"user_id": str(row.user_id)})
            raise Unauthorized("Session revoked. Please sign in again.", code="REFRESH_TOKEN_REUSED")
        # Within the grace window (e.g. two tabs refreshed together): issue a sibling token,
        # but only while the login is still alive (not logged out or revoked meanwhile).
        family_alive = await db.scalar(
            select(
                exists().where(
                    RefreshToken.family_id == row.family_id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > now,
                )
            )
        )
        if not family_alive:
            raise invalid

    if row.expires_at <= now:
        raise invalid

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid

    if row.replaced_at is None:
        row.replaced_at = now
        row.revoked_at = now
    new_row, raw = _new_refresh_row(
        user, client, family_id=row.family_id, absolute_expires_at=row.absolute_expires_at
    )
    db.add(new_row)
    await db.commit()
    return _issue(user, new_row, raw)


async def logout(db: AsyncSession, raw_token: str | None) -> None:
    if not raw_token:
        return
    row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if row is None:
        return
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


async def _create_auth_token(db: AsyncSession, user: User, purpose: AuthTokenPurpose, ttl: timedelta) -> str:
    """Invalidate older unused tokens of the same purpose, then add a new one (not committed)."""
    now = utcnow()
    await db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = generate_opaque_token()
    db.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + ttl))
    return raw


def _set_password_link(raw_token: str) -> str:
    return f"{get_settings().ADMIN_APP_URL.rstrip('/')}/set-password?token={raw_token}"


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Silently does nothing for unknown or inactive accounts (no enumeration)."""
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return
    settings = get_settings()
    raw = await _create_auth_token(
        db, user, AuthTokenPurpose.PASSWORD_RESET, timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES)
    )
    await db.commit()
    queue_email(
        "password_reset",
        user.email,
        {
            "full_name": user.full_name,
            "link": _set_password_link(raw),
            "expires_minutes": settings.PASSWORD_RESET_EXPIRE_MINUTES,
        },
    )


async def send_invite(db: AsyncSession, user: User, temp_password: str | None = None) -> None:
    """Create an invite token and email credentials + link. Commits."""
    settings = get_settings()
    raw = await _create_auth_token(
        db, user, AuthTokenPurpose.INVITE, timedelta(hours=settings.INVITE_EXPIRE_HOURS)
    )
    await db.commit()
    pwd = temp_password or "Coach#2026!"
    queue_email(
        "invite",
        user.email,
        {
            "full_name": user.full_name,
            "email": user.email,
            "password": pwd,
            "temp_password": pwd,
            "role": "Coach",
            "login_url": f"{settings.ADMIN_APP_URL.rstrip('/')}/login",
            "link": _set_password_link(raw),
            "expires_hours": settings.INVITE_EXPIRE_HOURS,
        },
    )


async def reset_password(db: AsyncSession, raw_token: str, new_password: str) -> None:
    """Consume a reset or invite token and set the password."""
    token = await db.scalar(
        select(AuthToken).where(AuthToken.token_hash == hash_token(raw_token)).with_for_update()
    )
    now = utcnow()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise BadRequest("This link is invalid or has expired", code="INVALID_OR_EXPIRED_TOKEN")
    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise BadRequest("This link is invalid or has expired", code="INVALID_OR_EXPIRED_TOKEN")

    _check_password_policy(new_password, user.email)
    token.used_at = now
    user.password_hash = hash_password(new_password)
    user.is_verified = True
    user.token_version += 1
    await _revoke_all_refresh_tokens(db, user.id)
    audit.record(
        db, f"{token.purpose.value}_COMPLETED", actor_user_id=user.id, entity_type="user", entity_id=user.id
    )
    await db.commit()


async def change_password(
    db: AsyncSession, user: User, current_password: str, new_password: str, client: ClientInfo
) -> IssuedTokens:
    valid, _ = verify_password(current_password, user.password_hash)
    if not valid:
        raise BadRequest("Current password is incorrect", code="INVALID_CURRENT_PASSWORD")
    _check_password_policy(new_password, user.email)

    user.password_hash = hash_password(new_password)
    user.token_version += 1  # every other device is signed out
    await _revoke_all_refresh_tokens(db, user.id)
    row, raw = _new_refresh_row(user, client)
    db.add(row)
    audit.record(db, "PASSWORD_CHANGED", actor_user_id=user.id, entity_type="user", entity_id=user.id)
    await db.commit()
    return _issue(user, row, raw)
