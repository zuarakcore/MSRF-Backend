from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email as email_module
from app.core.timeutils import month_start, today_local
from app.modules.fees.models import FeeLedgerEntry
from app.modules.notifications.models import Notification
from app.modules.users.models import Role
from tests.conftest import DEFAULT_PASSWORD, UserFactory, bearer, login
from tests.factories import PDF_BYTES, coach_headers, make_coach, make_refs, make_student

API = "/api/v1"


async def _admin(client: AsyncClient, make_user: UserFactory) -> dict[str, str]:
    await make_user(email="admin@example.com", role=Role.ADMIN)
    return bearer(await login(client, "admin@example.com"))


def _student_body(cat: object, pt: object, tc: object, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "fullName": "Adarsh Nair",
        "gender": "MALE",
        "bloodGroup": "O+",
        "dateOfBirth": "2012-05-14",
        "phone": "+91 98470 12345",
        "admissionDate": today_local().isoformat(),
        "categoryId": str(cat.id),  # type: ignore[attr-defined]
        "programTypeId": str(pt.id),  # type: ignore[attr-defined]
        "trainingCenterId": str(tc.id),  # type: ignore[attr-defined]
        "batch": "MORNING",
        "monthlyFee": 2500,
        "parentName": "Ramesh Nair",
        "parentRelationship": "FATHER",
        "parentPhone": "9447012345",
    }
    body.update(overrides)
    return body


# --- reference data --------------------------------------------------------------------


async def test_reference_crud_and_unique_names(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin(client, make_user)
    created = await client.post(f"{API}/categories", json={"name": "U-13 Squad"}, headers=headers)
    assert created.status_code == 201
    dup = await client.post(f"{API}/categories", json={"name": "u-13 squad"}, headers=headers)
    assert dup.status_code == 409
    assert dup.json()["code"] == "NAME_EXISTS"
    listed = await client.get(f"{API}/categories", headers=headers)
    assert [c["name"] for c in listed.json()] == ["U-13 Squad"]


async def test_reference_in_use_cannot_be_deleted(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    await make_student(db, cat, pt, tc)
    response = await client.delete(f"{API}/categories/{cat.id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "IN_USE"


async def test_coach_can_read_but_not_write_reference_data(client: AsyncClient, db: AsyncSession) -> None:
    cat, _, _ = await make_refs(db)
    coach = await make_coach(db, [cat])
    headers = await coach_headers(client, coach)
    assert (await client.get(f"{API}/categories", headers=headers)).status_code == 200
    assert (await client.post(f"{API}/categories", json={"name": "X Y"}, headers=headers)).status_code == 403


# --- students ----------------------------------------------------------------------------


async def test_create_student_generates_codes_and_first_ledger_month(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    response = await client.post(f"{API}/students", json=_student_body(cat, pt, tc), headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    year = today_local().year
    assert body["studentCode"] == f"MSRF-{year}-001"
    assert body["admissionNumber"] == f"ADM-{year}-001"
    assert body["phone"] == "9847012345"  # normalised
    assert body["monthlyFee"] == 2500
    assert body["currentMonthFeeStatus"] in ("PENDING", "OVERDUE")
    assert body["totals"]["outstanding"] == 2500

    entry = await db.scalar(select(FeeLedgerEntry).where(FeeLedgerEntry.period == month_start(today_local())))
    assert entry is not None and entry.amount_due == Decimal("2500.00")
    # The acting admin is not notified about their own action.
    assert await db.scalar(select(Notification.id)) is None


async def test_student_validation_errors(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    body = _student_body(cat, pt, tc, parentPhone="12345", dateOfBirth="2099-01-01")
    response = await client.post(f"{API}/students", json=body, headers=headers)
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert {"parentPhone", "dateOfBirth"} <= fields


async def test_inactive_reference_rejected(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    await client.patch(f"{API}/categories/{cat.id}", json={"status": "INACTIVE"}, headers=headers)
    response = await client.post(f"{API}/students", json=_student_body(cat, pt, tc), headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == "REFERENCE_INACTIVE"


async def test_student_list_filters_and_search(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    other_cat, _, _ = await make_refs(db)
    await make_student(db, cat, pt, tc, full_name="Adarsh Nair")
    await make_student(db, other_cat, pt, tc, full_name="Devika Menon")

    everyone = (await client.get(f"{API}/students", headers=headers)).json()
    assert everyone["total"] == 2
    by_cat = (await client.get(f"{API}/students", params={"categoryId": str(cat.id)}, headers=headers)).json()
    assert [s["fullName"] for s in by_cat["items"]] == ["Adarsh Nair"]
    by_search = (await client.get(f"{API}/students", params={"search": "devi"}, headers=headers)).json()
    assert [s["fullName"] for s in by_search["items"]] == ["Devika Menon"]
    page = (await client.get(f"{API}/students", params={"pageSize": 1, "page": 2}, headers=headers)).json()
    assert page["pages"] == 2 and len(page["items"]) == 1


async def test_coach_cannot_use_admin_student_endpoints(client: AsyncClient, db: AsyncSession) -> None:
    cat, _, _ = await make_refs(db)
    coach = await make_coach(db, [cat])
    response = await client.get(f"{API}/students", headers=await coach_headers(client, coach))
    assert response.status_code == 403


async def test_csv_import_is_all_or_nothing(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    header = "Full Name,Gender,Date of Birth,Category,Program Type,Training Center,Parent Name,Parent Phone"
    good = f"Arjun K,MALE,2012-05-14,{cat.name},{pt.name},{tc.name},Krishnan,9447112233"
    bad = f"Meera S,FEMALE,2013-09-15,Unknown Category,{pt.name},{tc.name},Suresh,12"
    files = {"file": ("students.csv", f"{header}\n{good}\n{bad}\n".encode(), "text/csv")}

    response = await client.post(f"{API}/students/import", files=files, headers=headers)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert {(e["row"], e["field"]) for e in errors} >= {(3, "Category")}
    assert (await client.get(f"{API}/students", headers=headers)).json()["total"] == 0

    ok_files = {"file": ("students.csv", f"{header}\n{good}\n".encode(), "text/csv")}
    dry = await client.post(
        f"{API}/students/import", params={"dryRun": "true"}, files=ok_files, headers=headers
    )
    assert dry.json() == {"created": 1, "dryRun": True}
    assert (await client.get(f"{API}/students", headers=headers)).json()["total"] == 0
    real = await client.post(f"{API}/students/import", files=ok_files, headers=headers)
    assert real.json() == {"created": 1, "dryRun": False}


async def test_csv_export_neutralises_formulas(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    await make_student(db, cat, pt, tc, full_name='=HYPERLINK("http://evil")')
    response = await client.get(f"{API}/students/export", headers=headers)
    assert response.status_code == 200
    assert "'=HYPERLINK" in response.text


async def test_student_documents_validate_content(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    student = await make_student(db, cat, pt, tc)
    url = f"{API}/students/{student.id}/documents"

    fake = await client.post(
        url,
        data={"title": "Birth cert"},
        files={"file": ("cert.pdf", b"MZ\x90\x00 not a pdf")},
        headers=headers,
    )
    assert fake.status_code == 415

    ok = await client.post(
        url, data={"title": "Birth cert"}, files={"file": ("../../cert.pdf", PDF_BYTES)}, headers=headers
    )
    assert ok.status_code == 201
    doc = ok.json()
    assert doc["file"]["contentType"] == "application/pdf"
    assert doc["file"]["fileName"] == "cert.pdf"  # path parts stripped

    download = await client.get(doc["file"]["url"].replace("https://test", ""))
    assert download.status_code == 200
    assert download.content == PDF_BYTES
    tampered = doc["file"]["url"].replace("https://test", "")[:-4] + "0000"
    assert (await client.get(tampered)).status_code == 404


async def test_student_with_payments_cannot_be_deleted(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    student = await make_student(db, cat, pt, tc)
    today = today_local()
    await client.post(
        f"{API}/fees/payments",
        json={
            "studentId": str(student.id),
            "paidOn": today.isoformat(),
            "mode": "CASH",
            "allocations": [{"year": today.year, "month": today.month, "amount": 500}],
        },
        headers=headers,
    )
    response = await client.delete(f"{API}/students/{student.id}", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "STUDENT_HAS_HISTORY"


# --- coaches -----------------------------------------------------------------------------


async def test_create_coach_sends_invite_and_never_returns_a_password(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, _, _ = await make_refs(db)
    response = await client.post(
        f"{API}/coaches",
        json={
            "fullName": "Rajesh Varma",
            "email": "Rajesh@Example.com",
            "phone": "9847012345",
            "joinedDate": "2021-03-15",
            "experienceYears": 12,
            "categoryIds": [str(cat.id)],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["inviteStatus"] == "PENDING"
    assert body["tempPassword"] == "Coach#2026!"
    assert body["defaultPassword"] == "Coach#2026!"
    assert email_module.outbox[-1].to == "rajesh@example.com"

    assert "Password: " in email_module.outbox[-1].text
    pwd = email_module.outbox[-1].text.split("Password: ")[1].split("\n")[0].strip()

    # Login with credentials sent in the email
    token_str = await login(client, "rajesh@example.com", pwd)
    assert token_str

    token = email_module.outbox[-1].text.split("token=")[1].split()[0].rstrip(")")
    assert (
        await client.post(
            f"{API}/auth/reset-password", json={"token": token, "newPassword": "coach-password-1"}
        )
    ).status_code == 204
    me = await client.get(
        f"{API}/auth/me", headers=bearer(await login(client, "rajesh@example.com", "coach-password-1"))
    )
    assert me.json()["coachId"] == body["id"]

    duplicate = await client.post(
        f"{API}/coaches",
        json={
            "fullName": "Someone Else",
            "email": "rajesh@example.com",
            "phone": "9847012345",
            "joinedDate": "2021-03-15",
        },
        headers=headers,
    )
    assert duplicate.status_code == 409


async def test_deactivating_a_coach_cuts_access_immediately(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, _, _ = await make_refs(db)
    coach = await make_coach(db, [cat])
    coach_auth = await coach_headers(client, coach)
    assert (await client.get(f"{API}/coach/dashboard", headers=coach_auth)).status_code == 200

    await client.patch(f"{API}/coaches/{coach.id}", json={"status": "INACTIVE"}, headers=headers)
    assert (await client.get(f"{API}/coach/dashboard", headers=coach_auth)).status_code == 401

    # Attempting to log in as an inactive coach must be forbidden
    login_attempt = await client.post(
        f"{API}/auth/login", json={"email": coach.user.email, "password": DEFAULT_PASSWORD}
    )
    assert login_attempt.status_code == 403
    assert login_attempt.json()["code"] == "ACCOUNT_INACTIVE"


async def test_edit_coach_credentials_sends_email_only_when_credentials_change(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, _, _ = await make_refs(db)
    coach = await make_coach(db, [cat], email="coach_edit@example.com")
    email_module.outbox.clear()

    # 1. Edit non-credential field (e.g. phone/bio) -> no email sent
    res1 = await client.patch(
        f"{API}/coaches/{coach.id}", json={"phone": "9998887776", "bio": "Updated bio text"}, headers=headers
    )
    assert res1.status_code == 200
    assert len(email_module.outbox) == 0

    # 2. Edit credentials (password) -> credentials_updated email sent
    res2 = await client.patch(
        f"{API}/coaches/{coach.id}", json={"password": "NewCoachPassword#2026"}, headers=headers
    )
    assert res2.status_code == 200
    assert len(email_module.outbox) == 1
    assert "Credentials Have Been Updated" in email_module.outbox[-1].subject
    assert "NewCoachPassword#2026" in email_module.outbox[-1].text

    # Login with new password works
    token_str = await login(client, "coach_edit@example.com", "NewCoachPassword#2026")
    assert token_str


async def test_coach_student_view_is_scoped_and_reduced(client: AsyncClient, db: AsyncSession) -> None:
    mine, pt, tc = await make_refs(db)
    other, _, _ = await make_refs(db)
    coach = await make_coach(db, [mine])
    visible = await make_student(db, mine, pt, tc)
    hidden = await make_student(db, other, pt, tc)
    headers = await coach_headers(client, coach)

    listed = (await client.get(f"{API}/coach/students", headers=headers)).json()
    assert [s["id"] for s in listed["items"]] == [str(visible.id)]
    detail = await client.get(f"{API}/coach/students/{visible.id}", headers=headers)
    assert detail.status_code == 200
    assert "monthlyFee" not in detail.json() and "address" not in detail.json()
    assert (await client.get(f"{API}/coach/students/{hidden.id}", headers=headers)).status_code == 404


async def test_unknown_birth_year_filter_and_dates(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin(client, make_user)
    response = await client.get(f"{API}/students", params={"birthYear": 1800}, headers=headers)
    assert response.status_code == 422


async def test_missing_photos_use_the_default_image(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    await make_student(db, cat, pt, tc)
    await make_coach(db, [cat])
    student = (await client.get(f"{API}/students", headers=headers)).json()["items"][0]
    coach = (await client.get(f"{API}/coaches", headers=headers)).json()["items"][0]
    for photo in (student["photo"], coach["photo"]):
        assert photo["isDefault"] is True
        assert photo["id"] is None
        assert photo["url"].endswith("/static/default-image.png")

    await client.post(
        f"{API}/team-members", json={"name": "No Photo", "designation": "Director"}, headers=headers
    )
    public = (await client.get(f"{API}/public/team-members")).json()
    assert public[0]["photoUrl"].endswith("/static/default-image.png")
