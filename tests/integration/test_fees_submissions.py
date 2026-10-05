from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import add_months, month_start, today_local
from app.modules.fees import ledger
from app.modules.fees.models import FeeLedgerEntry
from app.modules.notifications.models import Notification
from app.modules.users.models import Role
from tests.conftest import UserFactory, bearer, login
from tests.factories import make_refs, make_student, png_bytes

API = "/api/v1"


async def _ctx(client: AsyncClient, make_user: UserFactory, db: AsyncSession) -> dict[str, Any]:
    await make_user(email="admin@example.com", role=Role.ADMIN)
    headers = bearer(await login(client, "admin@example.com"))
    cat, pt, tc = await make_refs(db)
    student = await make_student(db, cat, pt, tc, monthly_fee=Decimal("2000"), parent_phone="9447012345")
    today = today_local()
    return {"headers": headers, "student": student, "year": today.year, "month": today.month, "today": today}


def _payment(ctx: dict[str, Any], amount: int, **extra: Any) -> dict[str, Any]:
    body = {
        "studentId": str(ctx["student"].id),
        "paidOn": ctx["today"].isoformat(),
        "mode": "UPI",
        "allocations": [{"year": ctx["year"], "month": ctx["month"], "amount": amount}],
    }
    body.update(extra)
    return body


async def test_partial_payment_discount_and_ledger_status(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    ctx = await _ctx(client, make_user, db)
    h = ctx["headers"]
    first = await client.post(f"{API}/fees/payments", json=_payment(ctx, 1500), headers=h)
    assert first.status_code == 201, first.text
    assert first.json()["receiptNumber"] == f"MSRF-RCPT-{ctx['year']}-00001"
    assert first.json()["allocations"][0]["amount"] == 1500

    params = {"year": ctx["year"], "month": ctx["month"]}
    row = (await client.get(f"{API}/fees/ledger", params=params, headers=h)).json()["items"][0]
    assert (row["amountPaid"], row["outstanding"]) == (1500, 500)
    assert row["status"] in ("PENDING", "OVERDUE")

    discount = {"year": ctx["year"], "month": ctx["month"], "amount": 200, "reason": "Sibling discount"}
    second = await client.post(f"{API}/fees/payments", json=_payment(ctx, 300, discount=discount), headers=h)
    assert second.status_code == 201
    assert second.json()["receiptNumber"].endswith("00002")

    paid = (await client.get(f"{API}/fees/ledger", params=params | {"status": "PAID"}, headers=h)).json()
    assert paid["total"] == 1 and paid["items"][0]["discount"] == 200
    summary = (await client.get(f"{API}/fees/summary", params=params, headers=h)).json()
    assert summary == {
        "expected": 2000,
        "collected": 1800,
        "discount": 200,
        "outstanding": 0,
        "studentCount": 1,
        "pendingCount": 0,
        "overdueCount": 0,
    }


async def test_overpayment_and_unknown_month_rejected(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    ctx = await _ctx(client, make_user, db)
    over = await client.post(f"{API}/fees/payments", json=_payment(ctx, 2001), headers=ctx["headers"])
    assert over.status_code == 422 and over.json()["code"] == "OVERPAYMENT"

    future = add_months(month_start(ctx["today"]), 1)
    body = _payment(ctx, 100)
    body["allocations"] = [{"year": future.year, "month": future.month, "amount": 100}]
    missing = await client.post(f"{API}/fees/payments", json=body, headers=ctx["headers"])
    assert missing.status_code == 404 and missing.json()["code"] == "LEDGER_MONTH_NOT_FOUND"

    dupe = _payment(ctx, 100)
    dupe["allocations"] = dupe["allocations"] * 2
    assert (await client.post(f"{API}/fees/payments", json=dupe, headers=ctx["headers"])).status_code == 422


async def test_void_reverses_allocations_once(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    ctx = await _ctx(client, make_user, db)
    payment = (
        await client.post(f"{API}/fees/payments", json=_payment(ctx, 2000), headers=ctx["headers"])
    ).json()
    url = f"{API}/payments/{payment['id']}/void"
    voided = await client.post(url, json={"reason": "Entered twice"}, headers=ctx["headers"])
    assert voided.status_code == 200 and voided.json()["status"] == "VOIDED"
    entry = await db.scalar(select(FeeLedgerEntry).execution_options(populate_existing=True))
    assert entry is not None and entry.amount_paid == Decimal("0.00")
    again = await client.post(url, json={"reason": "Entered twice"}, headers=ctx["headers"])
    assert again.status_code == 409


async def test_student_fee_year_view(client: AsyncClient, make_user: UserFactory, db: AsyncSession) -> None:
    ctx = await _ctx(client, make_user, db)
    await client.post(f"{API}/fees/payments", json=_payment(ctx, 2000), headers=ctx["headers"])
    fees = (await client.get(f"{API}/students/{ctx['student'].id}/fees", headers=ctx["headers"])).json()
    assert fees["monthlyFee"] == 2000
    month = fees["months"][-1]
    assert month["status"] == "PAID" and month["payments"][0]["amount"] == 2000


async def test_monthly_ledger_generation_is_idempotent(make_user: UserFactory, db: AsyncSession) -> None:
    cat, pt, tc = await make_refs(db)
    await make_student(db, cat, pt, tc)
    next_month = add_months(month_start(today_local()), 1)
    first = await db.execute(ledger.generate_month_stmt(next_month))
    second = await db.execute(ledger.generate_month_stmt(next_month))
    assert (first.rowcount, second.rowcount) == (1, 0)  # type: ignore[attr-defined]
    count = await db.scalar(select(func.count()).where(FeeLedgerEntry.period == next_month))
    assert count == 1


# --- parent payment submissions ------------------------------------------------------------


def _form(**overrides: str) -> dict[str, str]:
    data = {
        "studentName": "Adarsh Nair",
        "parentMobile": "+91 94470 12345",
        "paymentMethod": "UPI",
        "amount": "2000",
        "paymentDate": today_local().isoformat(),
        "transactionReference": "UPI/123/PAY",
    }
    data.update(overrides)
    return data


async def test_public_submission_flow_and_verification(
    client: AsyncClient, make_user: UserFactory, db: AsyncSession
) -> None:
    ctx = await _ctx(client, make_user, db)
    url = f"{API}/public/payment-submissions"

    no_shot = await client.post(url, data=_form())
    assert no_shot.status_code == 422 and no_shot.json()["code"] == "SCREENSHOT_REQUIRED"
    cash_no_receiver = await client.post(url, data=_form(paymentMethod="CASH"))
    assert cash_no_receiver.status_code == 422
    fake_png = await client.post(
        url, data=_form(), files={"screenshot": ("pay.png", b"<script>alert(1)</script>")}
    )
    assert fake_png.status_code == 415

    created = await client.post(url, data=_form(), files={"screenshot": ("pay.png", png_bytes())})
    assert created.status_code == 201, created.text
    assert created.json()["submissionNumber"].startswith("SUB-")
    assert await db.scalar(select(func.count()).select_from(Notification)) == 1  # the admin

    h = ctx["headers"]
    pending = (await client.get(f"{API}/payment-submissions", headers=h)).json()["items"]
    sub_id = pending[0]["id"]
    detail = (await client.get(f"{API}/payment-submissions/{sub_id}", headers=h)).json()
    assert detail["parentMobile"] == "9447012345"
    assert [c["id"] for c in detail["candidateStudents"]] == [str(ctx["student"].id)]
    assert detail["screenshot"]["url"].startswith("https://test/api/v1/files/")

    allocation = [{"year": ctx["year"], "month": ctx["month"], "amount": 1500}]
    mismatch = await client.post(
        f"{API}/payment-submissions/{sub_id}/verify",
        json={"studentId": str(ctx["student"].id), "allocations": allocation},
        headers=h,
    )
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "ALLOCATION_MISMATCH"

    allocation[0]["amount"] = 2000
    verified = await client.post(
        f"{API}/payment-submissions/{sub_id}/verify",
        json={"studentId": str(ctx["student"].id), "allocations": allocation},
        headers=h,
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED" and verified.json()["paymentId"]
    again = await client.post(
        f"{API}/payment-submissions/{sub_id}/reject", json={"reason": "Duplicate"}, headers=h
    )
    assert again.status_code == 409


async def test_public_submissions_are_rate_limited(client: AsyncClient, db: AsyncSession) -> None:
    url = f"{API}/public/payment-submissions"
    for _ in range(5):
        response = await client.post(url, data=_form(paymentMethod="CASH", handedOverTo="Office"))
        assert response.status_code == 201
    blocked = await client.post(url, data=_form(paymentMethod="CASH", handedOverTo="Office"))
    assert blocked.status_code == 429
