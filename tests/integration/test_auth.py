from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email as email_module
from app.core.config import get_settings
from app.core.timeutils import utcnow
from app.modules.auth import service as auth_service
from app.modules.auth.models import AuthToken, RefreshToken
from app.modules.users.models import Role
from tests.conftest import ALLOWED_ORIGIN, DEFAULT_PASSWORD, UserFactory, bearer, login

AUTH = "/api/v1/auth"
COOKIE = get_settings().REFRESH_COOKIE_NAME


async def _refresh_with(client: AsyncClient, refresh_token: str) -> int:
    """Present a specific refresh token, bypassing whatever the cookie jar holds."""
    client.cookies.clear()
    response = await client.post(f"{AUTH}/refresh", headers={"Cookie": f"{COOKIE}={refresh_token}"})
    return response.status_code


def _token_from_last_email() -> str:
    link = email_module.outbox[-1].text.split("http://admin.test/set-password?token=")[1].split()[0]
    return parse_qs(urlparse(f"http://x/?token={link}").query)["token"][0]


# --- login -------------------------------------------------------------------------


async def test_login_returns_access_token_and_sets_httponly_refresh_cookie(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(role=Role.COACH, email="coach@example.com")
    response = await client.post(
        f"{AUTH}/login", json={"email": "Coach@Example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tokenType"] == "bearer"
    assert body["user"] == {
        "id": str(user.id),
        "email": "coach@example.com",
        "fullName": "Test User",
        "role": "COACH",
        "isActive": True,
        "coachId": None,
        "lastLoginAt": body["user"]["lastLoginAt"],
    }
    cookie = response.headers["set-cookie"]
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/v1/auth"):
        assert attribute.lower() in cookie.lower()
    assert "passwordHash" not in response.text


@pytest.mark.parametrize("email", ["known@example.com", "unknown@example.com"])
async def test_login_failure_is_identical_for_unknown_and_wrong_password(
    client: AsyncClient, make_user: UserFactory, email: str
) -> None:
    await make_user(email="known@example.com")
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password", "code": "INVALID_CREDENTIALS"}


async def test_login_inactive_account(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="old@example.com", is_active=False)
    response = await client.post(
        f"{AUTH}/login", json={"email": "old@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_INACTIVE"


async def test_invited_user_without_password_cannot_log_in(
    client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user(email="invited@example.com", password=None)
    response = await client.post(
        f"{AUTH}/login", json={"email": "invited@example.com", "password": "anything-at-all"}
    )
    assert response.status_code == 401


async def test_login_rejects_unknown_fields_such_as_role(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="a@example.com", role=Role.COACH)
    response = await client.post(
        f"{AUTH}/login", json={"email": "a@example.com", "password": DEFAULT_PASSWORD, "role": "ADMIN"}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "role"


async def test_login_from_disallowed_origin_is_refused(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="a@example.com")
    response = await client.post(
        f"{AUTH}/login",
        json={"email": "a@example.com", "password": DEFAULT_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


async def test_login_from_allowed_origin_works(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="a@example.com")
    response = await client.post(
        f"{AUTH}/login",
        json={"email": "a@example.com", "password": DEFAULT_PASSWORD},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200


async def test_login_is_rate_limited_per_ip_and_email(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="target@example.com")
    for _ in range(5):
        response = await client.post(
            f"{AUTH}/login", json={"email": "target@example.com", "password": "nope-nope"}
        )
        assert response.status_code == 401
    response = await client.post(
        f"{AUTH}/login", json={"email": "target@example.com", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert int(response.headers["retry-after"]) >= 1


# --- current user / access tokens ----------------------------------------------------


async def test_me_requires_token(client: AsyncClient) -> None:
    response = await client.get(f"{AUTH}/me")
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_returns_current_user(client: AsyncClient, make_user: UserFactory) -> None:
    user = await make_user(email="me@example.com")
    token = await login(client, "me@example.com")
    response = await client.get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)


async def test_deactivation_takes_effect_immediately(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    user = await make_user(email="me@example.com")
    token = await login(client, "me@example.com")
    user.is_active = False
    await db.flush()
    response = await client.get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 403
    assert response.json()["code"] == "ACCOUNT_INACTIVE"


async def test_token_version_bump_invalidates_access_tokens(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    user = await make_user(email="me@example.com")
    token = await login(client, "me@example.com")
    user.token_version += 1
    await db.flush()
    response = await client.get(f"{AUTH}/me", headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["code"] == "TOKEN_INVALID"


# --- refresh / logout -----------------------------------------------------------------


async def test_refresh_rotates_the_token(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="me@example.com")
    await login(client, "me@example.com")
    first_cookie = client.cookies[COOKIE]

    response = await client.post(f"{AUTH}/refresh")
    assert response.status_code == 200
    assert response.json()["accessToken"]
    assert client.cookies[COOKIE] != first_cookie


async def test_refresh_without_cookie_fails(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_REFRESH_TOKEN"


async def test_reused_refresh_token_revokes_the_whole_login(
    client: AsyncClient, make_user: UserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "REFRESH_REUSE_GRACE_SECONDS", 0)
    await make_user(email="me@example.com")
    await login(client, "me@example.com")
    stolen = client.cookies[COOKIE]
    assert (await client.post(f"{AUTH}/refresh")).status_code == 200
    legitimate = client.cookies[COOKIE]

    client.cookies.clear()
    replay = await client.post(f"{AUTH}/refresh", headers={"Cookie": f"{COOKIE}={stolen}"})
    assert replay.status_code == 401
    assert replay.json()["code"] == "REFRESH_TOKEN_REUSED"

    # The legitimate (newest) token is dead too: the attacker cannot keep a session.
    assert await _refresh_with(client, legitimate) == 401


async def test_concurrent_refresh_within_grace_window_is_allowed(
    client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user(email="me@example.com")
    await login(client, "me@example.com")
    original = client.cookies[COOKIE]
    assert (await client.post(f"{AUTH}/refresh")).status_code == 200

    assert await _refresh_with(client, original) == 200


async def test_logout_revokes_refresh_including_grace_window(
    client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user(email="me@example.com")
    await login(client, "me@example.com")
    original = client.cookies[COOKIE]
    assert (await client.post(f"{AUTH}/refresh")).status_code == 200

    response = await client.post(f"{AUTH}/logout")
    assert response.status_code == 204
    assert COOKIE not in client.cookies

    assert await _refresh_with(client, original) == 401


async def test_refresh_fails_after_expiry(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    await make_user(email="me@example.com")
    await login(client, "me@example.com")
    await db.execute(update(RefreshToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert (await client.post(f"{AUTH}/refresh")).status_code == 401


async def test_refresh_fails_for_deactivated_user(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    user = await make_user(email="me@example.com")
    await login(client, "me@example.com")
    user.is_active = False
    await db.flush()
    assert (await client.post(f"{AUTH}/refresh")).status_code == 401


# --- forgot / reset password -----------------------------------------------------------


async def test_forgot_password_for_unknown_email_returns_202_and_sends_nothing(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/forgot-password", json={"email": "nobody@example.com"})
    assert response.status_code == 202
    assert email_module.outbox == []


async def test_password_reset_flow(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="me@example.com")
    old_access = await login(client, "me@example.com")

    response = await client.post(f"{AUTH}/forgot-password", json={"email": "me@example.com"})
    assert response.status_code == 202
    assert len(email_module.outbox) == 1
    assert email_module.outbox[0].to == "me@example.com"
    token = _token_from_last_email()

    response = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "newPassword": "a-brand-new-password"}
    )
    assert response.status_code == 204

    # Old sessions are gone; the new password works; the old one does not.
    assert (await client.get(f"{AUTH}/me", headers=bearer(old_access))).status_code == 401
    assert (await client.post(f"{AUTH}/refresh")).status_code == 401
    await login(client, "me@example.com", "a-brand-new-password")
    bad = await client.post(f"{AUTH}/login", json={"email": "me@example.com", "password": DEFAULT_PASSWORD})
    assert bad.status_code == 401

    # Single use.
    again = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "newPassword": "yet-another-password"}
    )
    assert again.status_code == 400
    assert again.json()["code"] == "INVALID_OR_EXPIRED_TOKEN"


async def test_requesting_a_new_reset_link_invalidates_the_previous_one(
    client: AsyncClient, make_user: UserFactory
) -> None:
    await make_user(email="me@example.com")
    await client.post(f"{AUTH}/forgot-password", json={"email": "me@example.com"})
    first = _token_from_last_email()
    await client.post(f"{AUTH}/forgot-password", json={"email": "me@example.com"})
    response = await client.post(
        f"{AUTH}/reset-password", json={"token": first, "newPassword": "a-new-password-1"}
    )
    assert response.status_code == 400


async def test_expired_reset_token_rejected(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    await make_user(email="me@example.com")
    await client.post(f"{AUTH}/forgot-password", json={"email": "me@example.com"})
    token = _token_from_last_email()
    await db.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    response = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "newPassword": "a-new-password-1"}
    )
    assert response.status_code == 400


async def test_reset_password_enforces_length(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/reset-password", json={"token": "x" * 43, "newPassword": "short"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "newPassword"


async def test_invite_flow_sets_password_and_verifies_user(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    coach = await make_user(email="newcoach@example.com", role=Role.COACH, password=None)
    await auth_service.send_invite(db, coach)
    assert email_module.outbox[-1].subject.startswith("You're invited")
    token = _token_from_last_email()

    response = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "newPassword": "coach-password-1"}
    )
    assert response.status_code == 204
    await login(client, "newcoach@example.com", "coach-password-1")
    await db.refresh(coach)
    assert coach.is_verified is True


async def test_password_equal_to_email_rejected(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="longaddress@example.com")
    await client.post(f"{AUTH}/forgot-password", json={"email": "longaddress@example.com"})
    token = _token_from_last_email()
    response = await client.post(
        f"{AUTH}/reset-password", json={"token": token, "newPassword": "LongAddress@example.com"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "WEAK_PASSWORD"


# --- change password -------------------------------------------------------------------


async def test_change_password_signs_out_other_sessions(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    await make_user(email="me@example.com")
    other_device = await login(client, "me@example.com")
    current = await login(client, "me@example.com")

    response = await client.post(
        f"{AUTH}/change-password",
        json={"currentPassword": DEFAULT_PASSWORD, "newPassword": "a-new-password-1"},
        headers=bearer(current),
    )
    assert response.status_code == 200
    new_access = response.json()["accessToken"]

    assert (await client.get(f"{AUTH}/me", headers=bearer(other_device))).status_code == 401
    assert (await client.get(f"{AUTH}/me", headers=bearer(new_access))).status_code == 200
    active = (await db.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))).all()
    assert len(active) == 1


async def test_change_password_requires_current_password(client: AsyncClient, make_user: UserFactory) -> None:
    await make_user(email="me@example.com")
    token = await login(client, "me@example.com")
    response = await client.post(
        f"{AUTH}/change-password",
        json={"currentPassword": "not-my-password", "newPassword": "a-new-password-1"},
        headers=bearer(token),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CURRENT_PASSWORD"


async def test_dependency_rate_limit_is_enforced_per_ip(client: AsyncClient) -> None:
    """Regression: a sync RateLimit.__call__ once returned an un-awaited coroutine and enforced nothing."""
    for n in range(10):
        response = await client.post(f"{AUTH}/forgot-password", json={"email": f"person{n}@example.com"})
        assert response.status_code == 202
    response = await client.post(f"{AUTH}/forgot-password", json={"email": "person99@example.com"})
    assert response.status_code == 429
