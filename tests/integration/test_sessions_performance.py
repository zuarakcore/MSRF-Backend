"""Coach portal rules: category scope, one session per day, 7-day lock, creator-only edits, IDOR."""

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import today_local
from app.modules.performance.models import Skill
from app.modules.sessions.models import SessionCoach, StudentAttendance, TrainingSession
from app.modules.users.models import Role
from tests.conftest import UserFactory, bearer, login
from tests.factories import coach_headers, days_ago, make_coach, make_refs, make_student, session_body

API = "/api/v1"


async def _setup(db: AsyncSession) -> dict[str, Any]:
    cat, pt, tc = await make_refs(db)
    other_cat, _, _ = await make_refs(db)
    coach = await make_coach(db, [cat])
    colleague = await make_coach(db, [cat])
    outsider = await make_coach(db, [other_cat])
    s1 = await make_student(db, cat, pt, tc, full_name="Adarsh Nair")
    s2 = await make_student(db, cat, pt, tc, full_name="Devika Menon")
    foreign = await make_student(db, other_cat, pt, tc, full_name="Other Category Kid")
    return locals()


def _attendance(*students: Any) -> list[dict[str, str]]:
    return [{"studentId": str(s.id), "status": "PRESENT"} for s in students]


async def test_roster_is_limited_to_assigned_categories(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _setup(db)
    headers = await coach_headers(client, ctx["coach"])
    ok = await client.get(
        f"{API}/coach/session-roster", params={"categoryIds": str(ctx["cat"].id)}, headers=headers
    )
    assert [s["fullName"] for s in ok.json()["students"]] == ["Adarsh Nair", "Devika Menon"]
    denied = await client.get(
        f"{API}/coach/session-roster", params={"categoryIds": str(ctx["other_cat"].id)}, headers=headers
    )
    assert denied.status_code == 422
    assert denied.json()["code"] == "CATEGORY_NOT_ASSIGNED"


async def test_create_session_with_attendance_and_co_coach(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _setup(db)
    headers = await coach_headers(client, ctx["coach"])
    body = session_body(
        [ctx["cat"].id], _attendance(ctx["s1"], ctx["s2"]), coCoachIds=[str(ctx["colleague"].id)]
    )
    body["attendance"][1]["status"] = "ABSENT"
    response = await client.post(f"{API}/coach/sessions", json=body, headers=headers)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["counts"] == {"present": 1, "absent": 1, "informed": 0, "total": 2}
    assert {c["role"] for c in data["coaches"]} == {"CREATOR", "CO_COACH"}
    assert all(c["status"] == "PRESENT" for c in data["coaches"])
    assert data["canEdit"] is True
    assert [sp["position"] for sp in data["splits"]] == [1]


async def test_one_session_per_coach_per_day_including_co_coaches(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _setup(db)
    body = session_body([ctx["cat"].id], coCoachIds=[str(ctx["colleague"].id)])
    first = await client.post(
        f"{API}/coach/sessions", json=body, headers=await coach_headers(client, ctx["coach"])
    )
    assert first.status_code == 201
    # The colleague was a co-coach today, so they cannot log their own session today.
    second = await client.post(
        f"{API}/coach/sessions",
        json=session_body([ctx["cat"].id]),
        headers=await coach_headers(client, ctx["colleague"]),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "SESSION_EXISTS_FOR_DATE"


async def test_session_rejects_students_outside_categories_and_bad_dates(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _setup(db)
    headers = await coach_headers(client, ctx["coach"])
    foreign = await client.post(
        f"{API}/coach/sessions",
        json=session_body([ctx["cat"].id], _attendance(ctx["foreign"])),
        headers=headers,
    )
    assert foreign.status_code == 422
    assert foreign.json()["code"] == "STUDENT_NOT_IN_SESSION_CATEGORIES"

    for day in (days_ago(8), (today_local() + timedelta(days=1)).isoformat()):
        response = await client.post(
            f"{API}/coach/sessions", json=session_body([ctx["cat"].id], sessionDate=day), headers=headers
        )
        assert response.status_code == 422
        assert response.json()["code"] == "DATE_OUT_OF_RANGE"


async def test_session_visibility_and_edit_rights(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _setup(db)
    body = session_body([ctx["cat"].id], _attendance(ctx["s1"]), coCoachIds=[str(ctx["colleague"].id)])
    created = await client.post(
        f"{API}/coach/sessions", json=body, headers=await coach_headers(client, ctx["coach"])
    )
    session_id = created.json()["id"]
    url = f"{API}/coach/sessions/{session_id}"

    # Outsider: cannot even see it (no existence leak).
    outsider = await coach_headers(client, ctx["outsider"])
    assert (await client.get(url, headers=outsider)).status_code == 404
    assert (await client.delete(url, headers=outsider)).status_code == 404
    # Co-coach: can see, cannot change.
    colleague = await coach_headers(client, ctx["colleague"])
    seen = await client.get(url, headers=colleague)
    assert seen.status_code == 200 and seen.json()["canEdit"] is False
    assert (await client.put(url, json=body, headers=colleague)).status_code == 403


async def test_update_replaces_children_and_lock_after_seven_days(
    client: AsyncClient, db: AsyncSession
) -> None:
    ctx = await _setup(db)
    headers = await coach_headers(client, ctx["coach"])
    created = await client.post(
        f"{API}/coach/sessions", json=session_body([ctx["cat"].id], _attendance(ctx["s1"])), headers=headers
    )
    session_id = created.json()["id"]
    url = f"{API}/coach/sessions/{session_id}"

    new_body = session_body(
        [ctx["cat"].id],
        _attendance(ctx["s1"], ctx["s2"]),
        dailyTopic="Finishing",
        splits=[{"heading": "A", "durationMinutes": 10}, {"heading": "B", "durationMinutes": 20}],
    )
    updated = await client.put(url, json=new_body, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["dailyTopic"] == "Finishing"
    assert updated.json()["counts"]["total"] == 2
    assert [s["heading"] for s in updated.json()["splits"]] == ["A", "B"]

    old = today_local() - timedelta(days=8)
    await db.execute(update(TrainingSession).where(TrainingSession.id == session_id).values(session_date=old))
    await db.execute(
        update(SessionCoach).where(SessionCoach.session_id == session_id).values(session_date=old)
    )
    await db.execute(
        update(StudentAttendance).where(StudentAttendance.session_id == session_id).values(session_date=old)
    )
    locked = await client.delete(url, headers=headers)
    assert locked.status_code == 403
    assert locked.json()["code"] == "SESSION_LOCKED"


async def test_admin_attendance_views(client: AsyncClient, db: AsyncSession, make_user: UserFactory) -> None:
    ctx = await _setup(db)
    body = session_body([ctx["cat"].id], _attendance(ctx["s1"], ctx["s2"]))
    body["attendance"][1]["status"] = "ABSENT"
    await client.post(f"{API}/coach/sessions", json=body, headers=await coach_headers(client, ctx["coach"]))

    await make_user(email="admin@example.com", role=Role.ADMIN)
    admin = bearer(await login(client, "admin@example.com"))
    today = today_local()
    stream = await client.get(
        f"{API}/attendance/students", params={"year": today.year, "month": today.month}, headers=admin
    )
    assert stream.json()["total"] == 2
    summary = await client.get(f"{API}/attendance/students/summary", headers=admin)
    bands = {i["student"]["fullName"]: (i["rate"], i["band"]) for i in summary.json()["items"]}
    assert bands == {"Adarsh Nair": (100.0, "GOOD"), "Devika Menon": (0.0, "CRITICAL")}
    coaches = await client.get(f"{API}/attendance/coaches", headers=admin)
    assert coaches.json()["total"] == 1
    future = await client.get(f"{API}/attendance/students", params={"year": today.year + 1}, headers=admin)
    assert future.status_code == 422
    csv = await client.get(f"{API}/attendance/students/export", headers=admin)
    assert csv.status_code == 200 and "Adarsh Nair" in csv.text


# --- performance reports -----------------------------------------------------------------


def _report(student_id: Any, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "studentId": str(student_id),
        "reportPeriod": "Monthly Evaluation - Sep 2026",
        "recordedDate": today_local().isoformat(),
        "position": "Central Midfielder",
        "strongFoot": "RIGHT",
        "skills": [{"skill": s.value, "rating": 4} for s in Skill],
        "strengths": "Vision",
        "areasForImprovement": "Weak foot",
        "developmentGoals": ["Improve weak foot"],
        "coachRemarks": "Great month",
        "overallRating": 4,
    }
    body.update(overrides)
    return body


async def test_performance_report_rules(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _setup(db)
    headers = await coach_headers(client, ctx["coach"])
    url = f"{API}/coach/performance-reports"

    created = await client.post(url, json=_report(ctx["s1"].id), headers=headers)
    assert created.status_code == 201, created.text
    assert len(created.json()["skills"]) == 15
    assert created.json()["student"]["age"] >= 12

    assert (await client.post(url, json=_report(ctx["s1"].id), headers=headers)).status_code == 409
    missing_skill = _report(ctx["s2"].id, skills=[{"skill": "PASSING", "rating": 3}])
    assert (await client.post(url, json=missing_skill, headers=headers)).status_code == 422
    bad_goal = _report(ctx["s2"].id, developmentGoals=["Become famous"])
    assert (await client.post(url, json=bad_goal, headers=headers)).status_code == 422
    out_of_scope = await client.post(url, json=_report(ctx["foreign"].id), headers=headers)
    assert out_of_scope.status_code == 404  # never confirms the student exists

    report_id = created.json()["id"]
    outsider = await coach_headers(client, ctx["outsider"])
    assert (await client.get(f"{url}/{report_id}", headers=outsider)).status_code == 404

    updated = await client.put(
        f"{url}/{report_id}", json=_report(ctx["s1"].id, overallRating=5), headers=headers
    )
    assert updated.status_code == 200 and updated.json()["overallRating"] == 5


async def test_performance_config(client: AsyncClient, db: AsyncSession) -> None:
    ctx = await _setup(db)
    config = await client.get(
        f"{API}/performance-reports/config", headers=await coach_headers(client, ctx["coach"])
    )
    assert len(config.json()["skills"]) == 15
    assert config.json()["skills"][2]["label"] == "BALL CONTROL / FIRST TOUCH"
