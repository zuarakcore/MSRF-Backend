import io
from datetime import timedelta
from pathlib import Path

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email as email_module
from app.core.config import get_settings
from app.core.timeutils import today_local
from app.modules.notifications.models import Notification
from app.modules.users.models import Role
from tests.conftest import UserFactory, bearer, login
from tests.factories import PDF_BYTES, png_bytes

API = "/api/v1"


async def _admin(client: AsyncClient, make_user: UserFactory) -> dict[str, str]:
    await make_user(email="admin@example.com", role=Role.ADMIN)
    return bearer(await login(client, "admin@example.com"))


async def test_programmes_admin_and_public(client: AsyncClient, make_user: UserFactory) -> None:
    h = await _admin(client, make_user)
    body = {
        "title": "Grassroots Kids Football",
        "ageGroup": "6 – 10 years",
        "description": "Fun first",
        "benefits": ["Ball mastery", "  ", "Parent reports"],
        "date": "2026-09-14",
        "place": "MCFC Academy Ground, Kozhikode",
        "kind": "Trials",
        "registrationUrl": "/contact",
    }
    created = await client.post(f"{API}/programmes", json=body, headers=h)
    assert created.status_code == 201
    assert created.json()["slug"] == "grassroots-kids-football"
    assert created.json()["benefits"] == ["Ball mastery", "Parent reports"]
    assert created.json()["date"] == "2026-09-14"
    assert created.json()["place"] == "MCFC Academy Ground, Kozhikode"
    assert created.json()["kind"] == "Trials"
    assert created.json()["registrationUrl"] == "/contact"

    again = await client.post(f"{API}/programmes", json=body, headers=h)
    assert again.json()["slug"] == "grassroots-kids-football-2"
    await client.patch(f"{API}/programmes/{again.json()['id']}", json={"status": "INACTIVE"}, headers=h)

    public = (await client.get(f"{API}/public/programmes")).json()
    assert [p["slug"] for p in public] == ["grassroots-kids-football"]
    assert "status" not in public[0]
    assert public[0]["date"] == "2026-09-14"
    assert public[0]["place"] == "MCFC Academy Ground, Kozhikode"
    assert public[0]["kind"] == "Trials"
    assert public[0]["registrationUrl"] == "/contact"
    assert (await client.get(f"{API}/public/programmes/grassroots-kids-football-2")).status_code == 404


async def test_gallery_upload_creates_public_image_and_thumbnail(
    client: AsyncClient, make_user: UserFactory
) -> None:
    h = await _admin(client, make_user)
    created = await client.post(
        f"{API}/gallery-items",
        data={"title": "Match day", "category": "Matches", "isWide": "true"},
        files={"image": ("photo.png", png_bytes((3000, 1500)))},
        headers=h,
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["isWide"] is True
    assert item["image"]["url"].startswith("https://test/media/public/gallery_image/")

    public = (await client.get(f"{API}/public/gallery-items", params={"category": "matches"})).json()
    assert public["total"] == 1
    entry = public["items"][0]
    assert (entry["width"], entry["height"]) == (2000, 1000)  # resized to the 2000 px cap
    assert (await client.get(f"{API}/public/gallery-categories")).json() == ["Matches"]

    key = entry["thumbnailUrl"].split("/media/public/")[1]
    thumb = Image.open(Path(get_settings().MEDIA_ROOT) / "public" / key)
    assert max(thumb.size) == 600


async def test_uploaded_photos_lose_exif_metadata(client: AsyncClient, make_user: UserFactory) -> None:
    h = await _admin(client, make_user)
    response = await client.post(
        f"{API}/uploads",
        data={"purpose": "STUDENT_PHOTO"},
        files={"file": ("me.jpg", png_bytes(exif=True))},
        headers=h,
    )
    assert response.status_code == 201
    url = response.json()["url"]
    assert url.startswith("https://test/api/v1/files/")  # minors' photos are private, signed links only
    content = (await client.get(url.replace("https://test", ""))).content
    assert b"SecretCameraMaker" not in content
    assert Image.open(io.BytesIO(content)).getexif() == {}


async def test_jobs_and_applications(client: AsyncClient, make_user: UserFactory, db: AsyncSession) -> None:
    h = await _admin(client, make_user)
    today = today_local()
    job = (
        await client.post(
            f"{API}/jobs",
            json={
                "title": "Head Coach",
                "location": "Kozhikode",
                "description": "Lead",
                "postedOn": today.isoformat(),
            },
            headers=h,
        )
    ).json()
    closed = (
        await client.post(
            f"{API}/jobs",
            json={
                "title": "Scout",
                "location": "Kerala",
                "description": "Find",
                "postedOn": (today - timedelta(days=30)).isoformat(),
                "closingDate": (today - timedelta(days=1)).isoformat(),
            },
            headers=h,
        )
    ).json()
    await client.post(
        f"{API}/jobs",
        json={
            "title": "Future Job",
            "location": "Kerala",
            "description": "Starting next month",
            "postedOn": (today + timedelta(days=5)).isoformat(),
        },
        headers=h,
    )
    assert [j["title"] for j in (await client.get(f"{API}/public/jobs")).json()] == ["Head Coach"]

    form = {
        "fullName": "Vikram Sethi",
        "mobileNumber": "9845099887",
        "emailAddress": "v@example.com",
        "location": "Kochi",
    }
    applied = await client.post(
        f"{API}/public/jobs/{job['id']}/applications", data=form, files={"cv": ("cv.pdf", PDF_BYTES)}
    )
    assert applied.status_code == 201, applied.text
    late = await client.post(
        f"{API}/public/jobs/{closed['id']}/applications", data=form, files={"cv": ("cv.pdf", PDF_BYTES)}
    )
    assert late.status_code == 409 and late.json()["code"] == "JOB_CLOSED"
    not_a_cv = await client.post(
        f"{API}/public/jobs/{job['id']}/applications",
        data={**form, "emailAddress": "other@example.com"},  # a new applicant, but the file is not a CV
        files={"cv": ("cv.pdf", png_bytes())},
    )
    assert not_a_cv.status_code == 415

    apps = (await client.get(f"{API}/job-applications", headers=h)).json()
    assert apps["total"] == 1 and apps["items"][0]["position"] == "Head Coach"
    assert (await client.delete(f"{API}/jobs/{job['id']}", headers=h)).status_code == 409


async def test_public_enquiry_notifies_admins_and_auto_replies(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    h = await _admin(client, make_user)
    programme = (
        await client.post(
            f"{API}/programmes",
            json={"title": "Weekend Batch", "ageGroup": "8 – 16", "description": "Sat/Sun"},
            headers=h,
        )
    ).json()
    response = await client.post(
        f"{API}/public/enquiries",
        json={
            "parentName": "Rahul Menon",
            "playerName": "Arjun",
            "phone": "9895011223",
            "email": "rahul@example.com",
            "programmeId": programme["id"],
            "message": "Trial dates?",
        },
    )
    assert response.status_code == 201
    assert await db.scalar(select(func.count()).select_from(Notification)) == 1
    assert email_module.outbox[-1].to == "rahul@example.com"

    enquiries = (await client.get(f"{API}/enquiries", headers=h)).json()["items"]
    assert enquiries[0]["subject"] == "Weekend Batch (8 – 16)"
    unread = (await client.get(f"{API}/notifications/unread-count", headers=h)).json()
    assert unread == {"count": 1}
    await client.post(f"{API}/notifications/read-all", headers=h)
    assert (await client.get(f"{API}/notifications/unread-count", headers=h)).json() == {"count": 0}


async def test_dashboard_and_search(client: AsyncClient, make_user: UserFactory) -> None:
    h = await _admin(client, make_user)
    summary = await client.get(f"{API}/dashboard/summary", headers=h)
    assert summary.status_code == 200, summary.text
    assert summary.json()["students"] == {"total": 0, "active": 0}
    results = await client.get(f"{API}/search", params={"q": "ad"}, headers=h)
    assert set(results.json()) == {"students", "coaches", "paymentSubmissions", "payments"}


async def test_one_application_per_person_per_job(client: AsyncClient, make_user: UserFactory) -> None:
    h = await _admin(client, make_user)
    today = today_local().isoformat()
    jobs = [
        (
            await client.post(
                f"{API}/jobs",
                json={"title": t, "location": "Kozhikode", "description": "x", "postedOn": today},
                headers=h,
            )
        ).json()["id"]
        for t in ("Head Coach", "Scout")
    ]
    form = {
        "fullName": "Vikram Sethi",
        "mobileNumber": "9845099887",
        "emailAddress": "v@example.com",
        "location": "Kochi",
    }
    url = f"{API}/public/jobs/{{}}/applications"
    assert (
        await client.post(url.format(jobs[0]), data=form, files={"cv": ("cv.pdf", PDF_BYTES)})
    ).status_code == 201
    again = await client.post(
        url.format(jobs[0]),
        data={**form, "emailAddress": "V@Example.com"},
        files={"cv": ("cv.pdf", PDF_BYTES)},
    )
    assert again.status_code == 409 and again.json()["code"] == "ALREADY_APPLIED"
    assert (
        await client.post(url.format(jobs[1]), data=form, files={"cv": ("cv.pdf", PDF_BYTES)})
    ).status_code == 201
