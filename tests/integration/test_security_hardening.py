"""Regression tests for the security review fixes."""

import io

from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email as email_module
from app.core.timeutils import today_local
from app.modules.users.models import Role
from tests.conftest import UserFactory, bearer, login
from tests.factories import make_refs, make_student, png_bytes

API = "/api/v1"


async def _admin(client: AsyncClient, make_user: UserFactory) -> dict[str, str]:
    await make_user(email="admin@example.com", role=Role.ADMIN)
    return bearer(await login(client, "admin@example.com"))


async def test_oversized_body_is_refused_before_it_is_read(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/public/enquiries",
        content=b"x",
        headers={"Content-Length": str(50 * 1024 * 1024), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"


async def test_streamed_oversized_upload_is_cut_off(client: AsyncClient) -> None:
    """Chunked upload with no Content-Length: the stream is cut off once it passes the limit."""

    async def body():  # type: ignore[no-untyped-def]
        yield (
            b'--B\r\nContent-Disposition: form-data; name="screenshot"; filename="a.png"\r\n'
            b"Content-Type: image/png\r\n\r\n"
        )
        for _ in range(20):
            yield b"\x00" * (1024 * 1024)
        yield b"\r\n--B--\r\n"

    response = await client.post(
        f"{API}/public/payment-submissions",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=B"},
    )
    assert response.status_code == 413


async def test_search_wildcards_are_literal(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    headers = await _admin(client, make_user)
    cat, pt, tc = await make_refs(db)
    await make_student(db, cat, pt, tc, full_name="Adarsh Nair")
    await make_student(db, cat, pt, tc, full_name="100% Effort_Kid")
    for term, expected in (("%", ["100% Effort_Kid"]), ("_", ["100% Effort_Kid"]), ("a_a", []), ("\\", [])):
        result = await client.get(f"{API}/students", params={"search": term}, headers=headers)
        assert [s["fullName"] for s in result.json()["items"]] == expected, term


async def test_decompression_bomb_is_rejected(client: AsyncClient, make_user: UserFactory) -> None:
    headers = await _admin(client, make_user)
    bomb = io.BytesIO()
    Image.new("1", (8000, 8000)).save(bomb, format="PNG")  # 64 MP, a few KB compressed
    response = await client.post(
        f"{API}/uploads",
        data={"purpose": "TEAM_PHOTO"},
        files={"file": ("bomb.png", bomb.getvalue())},
        headers=headers,
    )
    assert response.status_code == 415
    assert response.json()["code"] == "IMAGE_TOO_LARGE"


async def test_enquiry_auto_reply_has_no_visitor_text_and_is_limited(client: AsyncClient) -> None:
    body = {
        "parentName": "Visit http://spam.example now",
        "phone": "9895011223",
        "email": "victim@example.com",
        "message": "spam",
    }
    for _ in range(3):
        assert (await client.post(f"{API}/public/enquiries", json=body)).status_code == 201
    sent = [m for m in email_module.outbox if m.to == "victim@example.com"]
    assert len(sent) == 2  # third enquiry is stored, but no further email to that address
    assert "spam.example" not in sent[0].text and "spam.example" not in sent[0].html


async def test_website_payment_form_casing_and_legacy_field(client: AsyncClient) -> None:
    data = {
        "studentName": "Adarsh Nair",
        "parentMobile": "9447012345",
        "paymentMethod": "Upi",
        "amount": "500",
        "paymentDate": today_local().isoformat(),
        "transactionId": "UPI/999/PAY",
    }
    response = await client.post(
        f"{API}/public/payment-submissions", data=data, files={"screenshot": ("p.png", png_bytes())}
    )
    assert response.status_code == 201, response.text
    cash = await client.post(
        f"{API}/public/payment-submissions",
        data={**data, "paymentMethod": "Cash", "handedOverTo": "Front office"},
    )
    assert cash.status_code == 201, cash.text


async def test_student_list_includes_fee_totals(
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
            "allocations": [{"year": today.year, "month": today.month, "amount": 1200}],
        },
        headers=headers,
    )
    row = (await client.get(f"{API}/students", headers=headers)).json()["items"][0]
    assert (row["feeDueToDate"], row["paidToDate"], row["outstanding"]) == (2000, 1200, 800)
