"""Query-count and latency benchmark for every GET endpoint (run against a populated database).

    .venv/bin/python -m scripts.benchmark_queries [--repeat 5]

Calls the app in-process (no network), authenticated as the first active ADMIN and the first COACH,
and prints SQL statements and median latency per endpoint. A high statement count means N+1
or redundant queries; it should stay constant as data grows.
"""

import argparse
import asyncio
import statistics
import time
import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from app.core.database import get_engine, get_sessionmaker
from app.core.security import create_access_token
from app.core.timeutils import today_local
from app.main import app
from app.modules.coaches.models import CoachProfile
from app.modules.users.models import Role, User

statements = 0


def _count(*_: Any) -> None:
    global statements
    statements += 1


async def _token(role: Role) -> tuple[dict[str, str], Any]:
    async with get_sessionmaker()() as db:
        user = await db.scalar(select(User).where(User.role == role, User.is_active.is_(True)).limit(1))
        assert user is not None, f"no active {role} user"
        coach = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user.id))
        token, _ = create_access_token(
            user_id=user.id, role=user.role.value, token_version=user.token_version
        )
        return {"Authorization": f"Bearer {token}"}, coach


async def _first_id(client: AsyncClient, headers: dict[str, str], path: str) -> str:
    data = (await client.get(path, headers=headers)).json()
    items = data["items"] if isinstance(data, dict) else data
    return str(items[0]["id"]) if items else str(uuid.uuid4())


async def main(repeat: int) -> None:
    from app.core.config import get_settings

    get_settings().RATE_LIMIT_ENABLED = False  # benchmark the queries, not the limiter
    event.listen(get_engine().sync_engine, "before_cursor_execute", _count)
    admin, _ = await _token(Role.ADMIN)
    coach, _ = await _token(Role.COACH)
    t = today_local()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        sid = await _first_id(c, admin, "/api/v1/students")
        cid = await _first_id(c, admin, "/api/v1/coaches")
        sess = await _first_id(c, admin, "/api/v1/sessions")
        rep = await _first_id(c, admin, "/api/v1/performance-reports")
        pay = await _first_id(c, admin, "/api/v1/payments")
        sub = await _first_id(c, admin, "/api/v1/payment-submissions")
        cstud = await _first_id(c, coach, "/api/v1/coach/students")
        ym = {"year": t.year, "month": t.month}
        cases: list[tuple[str, dict[str, str], dict[str, Any]]] = [
            ("/api/v1/dashboard/summary", admin, {}),
            ("/api/v1/students", admin, {}),
            ("/api/v1/students", admin, {"search": "a", "feeStatus": "PENDING"}),
            (f"/api/v1/students/{sid}", admin, {}),
            (f"/api/v1/students/{sid}/attendance", admin, {}),
            (f"/api/v1/students/{sid}/fees", admin, {}),
            (f"/api/v1/students/{sid}/payments", admin, {}),
            ("/api/v1/students/filter-options", admin, {}),
            ("/api/v1/coaches", admin, {}),
            (f"/api/v1/coaches/{cid}", admin, {}),
            (f"/api/v1/coaches/{cid}/attendance", admin, {}),
            ("/api/v1/categories", admin, {}),
            ("/api/v1/sessions", admin, {}),
            (f"/api/v1/sessions/{sess}", admin, {}),
            ("/api/v1/attendance/students", admin, ym),
            ("/api/v1/attendance/students/summary", admin, ym),
            ("/api/v1/attendance/coaches", admin, ym),
            ("/api/v1/performance-reports", admin, {}),
            (f"/api/v1/performance-reports/{rep}", admin, {}),
            ("/api/v1/fees/ledger", admin, ym),
            ("/api/v1/fees/ledger", admin, {"year": t.year}),
            ("/api/v1/fees/summary", admin, {"year": t.year}),
            ("/api/v1/payments", admin, {}),
            (f"/api/v1/payments/{pay}", admin, {}),
            ("/api/v1/payment-submissions", admin, {}),
            (f"/api/v1/payment-submissions/{sub}", admin, {}),
            ("/api/v1/programmes", admin, {}),
            ("/api/v1/team-members", admin, {}),
            ("/api/v1/gallery-items", admin, {}),
            ("/api/v1/jobs", admin, {}),
            ("/api/v1/job-applications", admin, {}),
            ("/api/v1/enquiries", admin, {}),
            ("/api/v1/notifications", admin, {}),
            ("/api/v1/search", admin, {"q": "an"}),
            ("/api/v1/coach/dashboard", coach, {}),
            ("/api/v1/coach/students", coach, {}),
            (f"/api/v1/coach/students/{cstud}", coach, {}),
            ("/api/v1/coach/sessions", coach, {}),
            ("/api/v1/coach/performance-reports", coach, {}),
            ("/api/v1/public/programmes", {}, {}),
            ("/api/v1/public/team-members", {}, {}),
            ("/api/v1/public/gallery-items", {}, {}),
            ("/api/v1/public/jobs", {}, {}),
        ]
        global statements
        print(f"{'endpoint':<58} {'status':>6} {'queries':>8} {'median ms':>10}")
        total_queries = 0
        for path, headers, params in cases:
            timings, counts, status = [], [], 0
            for _ in range(repeat):
                statements = 0
                start = time.perf_counter()
                r = await c.get(path, headers=headers, params={k: v for k, v in params.items() if v != ""})
                timings.append((time.perf_counter() - start) * 1000)
                counts.append(statements)
                status = r.status_code
            label = path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
            print(f"{label[:58]:<58} {status:>6} {counts[-1]:>8} {statistics.median(timings):>10.1f}")
            total_queries += counts[-1]
        print(f"{'TOTAL':<58} {'':>6} {total_queries:>8}")
    await get_engine().dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    asyncio.run(main(parser.parse_args().repeat))
