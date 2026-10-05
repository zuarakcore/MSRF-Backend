"""Demo activity for development: months of fees and payments, training sessions with attendance,
performance reports, gallery, job applications, enquiries, payment submissions and documents.

Run after `seed-dev`:  python -m app.cli seed-demo-activity
Refuses to run in production. Idempotent: does nothing if performance reports already exist.
Deterministic (fixed random seed). Sends no email.
"""

import io
import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from PIL import Image, ImageDraw
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.counters import next_code
from app.core.enums import AttendanceStatus, Batch, BloodGroup, Gender, RecordStatus, Relationship
from app.core.timeutils import add_months, month_start, today_local
from app.modules.coaches.models import CoachProfile
from app.modules.fees import ledger
from app.modules.fees import service as fees
from app.modules.fees.models import FeeLedgerEntry, PaymentMode
from app.modules.fees.schemas import AllocationIn, DiscountIn
from app.modules.files import service as files
from app.modules.files.models import FilePurpose
from app.modules.notifications import service as notifications
from app.modules.notifications.models import NotificationType
from app.modules.payment_submissions.models import PaymentSubmission, SubmissionMethod, SubmissionStatus
from app.modules.performance.models import (
    POSITIONS,
    STANDARD_GOALS,
    PerformanceReport,
    PerformanceSkillRating,
    Skill,
    StrongFoot,
)
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.sessions.models import (
    SessionCoach,
    SessionCoachRole,
    SessionSplit,
    StudentAttendance,
    TrainingSession,
)
from app.modules.students.models import Student, StudentDocument
from app.modules.users.models import Role, User
from app.modules.website.models import (
    ApplicationStatus,
    Enquiry,
    EnquiryStatus,
    Event,
    GalleryItem,
    Job,
    JobApplication,
    Programme,
)

HISTORY_MONTHS = 6
SESSION_DAYS = 45

EXTRA_STUDENTS = [
    ("Isha Patel", Gender.FEMALE, "Manish Patel"),
    ("Karthik Raja", Gender.MALE, "Shanmuga Raja"),
    ("Nikhil Prabhu", Gender.MALE, "Ganesh Prabhu"),
    ("Pooja Hegde", Gender.FEMALE, "Subhash Hegde"),
    ("Rahul V. S.", Gender.MALE, "Sivadasan V."),
    ("Ritu Sen", Gender.FEMALE, "Amit Sen"),
    ("Sneha Chacko", Gender.FEMALE, "Chacko Joseph"),
    ("Tarun Reddy", Gender.MALE, "Pratap Reddy"),
    ("Varun Nair", Gender.MALE, "Biju Nair"),
    ("Arya S. Kumar", Gender.FEMALE, "Suresh Kumar"),
    ("Faizal Khan", Gender.MALE, "Imtiaz Khan"),
    ("Gauri Shankar", Gender.FEMALE, "Shankar Iyer"),
    ("Harish R.", Gender.MALE, "Ramachandran"),
    ("Indu Lekha", Gender.FEMALE, "Janardhanan"),
    ("Jitendra Shah", Gender.MALE, "Kiran Shah"),
    ("Keerthi Suresh", Gender.FEMALE, "Suresh Kumar"),
    ("Leo Francis", Gender.MALE, "Francis Xavier"),
    ("Madhavan Unni", Gender.MALE, "Unnikrishnan P."),
]

TOPICS = [
    ("Tactical High-Pressing & Counter Attacks", "Midfield Transition & Defensive Recovery"),
    ("Possession Retention & One-Touch Passing", "Rondo 4v2 Overloads"),
    ("Defensive Structure & Zonal Positioning", "Back-Four Step-Up & Cover"),
    ("Speed, Agility & Plyometric Fitness", "Ladder Drills & Acceleration"),
    ("Finishing & 1v1 Attacking Scenarios", "Wing Overlaps & Cut-Back Finishing"),
    ("Set Piece Strategies", "Corner Routines & Zonal Marking"),
]
SPLITS = [
    ("Dynamic Warm-up & Agility", 15, "Joint mobility, ladder work and acceleration sprints."),
    ("Technical Drill", 25, "Passing triangles and first-touch orientation under pressure."),
    ("Small-Sided Game", 30, "7v7 half-pitch game focusing on the day's topic."),
    ("Cool Down & Debrief", 10, "Static stretching and individual feedback."),
]
VENUES = ["Main Stadium Ground Pitch A", "Pitch B Synthetic Turf", "Kozhikode Main Campus Ground 1"]
GALLERY = [
    ("Match day squad", "Matches", (0, 120, 60)),
    ("U-15 State Championship final", "Matches", (10, 90, 160)),
    ("Grassroots session", "Training", (30, 150, 70)),
    ("Small-sided games", "Training", (60, 130, 40)),
    ("Academy open trials", "Events", (160, 90, 20)),
    ("Annual awards night", "Events", (120, 40, 120)),
    ("Argentinos Juniors delegation visit", "Argentina", (40, 110, 200)),
    ("Coach clinic with AJ staff", "Argentina", (70, 140, 210)),
]
EVENTS = [
    (
        "Academy Open Trials",
        "Trials",
        datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
        "MCFC Academy Ground, Kozhikode",
        "/contact",
    ),
    (
        "Argentinos Juniors Coach Clinic",
        "Workshop",
        datetime(2026, 10, 5, 16, 0, tzinfo=UTC),
        "MSRF Training Centre, Keralam",
        "/contact",
    ),
    (
        "Challengers Youth Cup",
        "Tournament",
        datetime(2026, 11, 22, 8, 30, tzinfo=UTC),
        "Kozhikode District Stadium",
        "/contact",
    ),
]
APPLICANTS = [
    (
        "Vikram Sethi",
        "vikram.sethi@example.com",
        "9845099887",
        "Kochi",
        "Academy Head Coach",
        ApplicationStatus.UNDER_REVIEW,
    ),
    (
        "Dr. Sneha Roy",
        "sneha.roy@example.com",
        "9447188776",
        "Thrissur",
        "Sports Physiotherapist",
        ApplicationStatus.SHORTLISTED,
    ),
    (
        "Rahul Verma",
        "rahul.verma@example.com",
        "9895044332",
        "Kannur",
        "Youth Scout",
        ApplicationStatus.UNDER_REVIEW,
    ),
    (
        "Anoop Mathew",
        "anoop.mathew@example.com",
        "9746011223",
        "Kozhikode",
        "Academy Head Coach",
        ApplicationStatus.REJECTED,
    ),
]
ENQUIRIES = [
    (
        "Rahul Menon",
        "Arjun",
        "9895011223",
        "rahul.menon@example.com",
        0,
        "Interested in enrolling my 8-year-old son for the upcoming batch.",
        EnquiryStatus.NEW,
    ),
    (
        "Anjali Nair",
        "Diya",
        "9446033445",
        "anjali.nair@example.com",
        2,
        "Looking for trial dates and hostel facility details.",
        EnquiryStatus.CONTACTED,
    ),
    (
        "Suresh Babu",
        None,
        "9847055667",
        None,
        4,
        "Is there a weekend batch near Malappuram?",
        EnquiryStatus.NEW,
    ),
    (
        "Fathima Rasheed",
        "Ayaan",
        "9562077889",
        "fathima.r@example.com",
        3,
        "My son is a goalkeeper, 13 years old. What is the fee?",
        EnquiryStatus.RESOLVED,
    ),
    (
        "George Thomas",
        "Alan",
        "9745099001",
        None,
        1,
        "Can we visit the academy on Saturday?",
        EnquiryStatus.NEW,
    ),
]

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n"


def _image(title: str, color: tuple[int, int, int], size: tuple[int, int] = (1600, 1000)) -> bytes:
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    for i in range(0, size[0], 80):  # simple pitch stripes so thumbnails are not flat
        draw.rectangle([i, 0, i + 40, size[1]], fill=tuple(min(255, c + 18) for c in color))
    draw.text((60, size[1] - 120), title, fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _month_label(period: date) -> str:
    return period.strftime("%B %Y")


async def seed_activity(db: AsyncSession) -> dict[str, int]:
    if get_settings().is_production:
        raise RuntimeError("Refusing to seed demo data in production")
    if await db.scalar(select(PerformanceReport.id).limit(1)):
        return {}
    rng = random.Random(42)  # noqa: S311 - deterministic demo data, not security
    today = today_local()
    admin = await db.scalar(select(User).where(User.role == Role.ADMIN).order_by(User.created_at))
    if admin is None:
        raise RuntimeError("Run `seed-dev` (or create-admin) first")
    categories = list(await db.scalars(select(Category).order_by(Category.sort_order)))
    program_types = list(await db.scalars(select(ProgramType)))
    centers = list(await db.scalars(select(TrainingCenter)))
    coaches = list(await db.scalars(select(CoachProfile).order_by(CoachProfile.created_at)))
    if not categories or not coaches:
        raise RuntimeError("Run `seed-dev` first")
    stats: dict[str, int] = {}

    # --- more students, admitted over the last months --------------------------------------
    for i, (name, gender, parent) in enumerate(EXTRA_STUDENTS):
        admission = add_months(month_start(today), -rng.randint(1, HISTORY_MONTHS))
        student = Student(
            student_code=await next_code(db, "student", admission.year, "MSRF-{year}-{n:03d}"),
            admission_number=await next_code(db, "admission", admission.year, "ADM-{year}-{n:03d}"),
            admission_date=admission + timedelta(days=rng.randint(0, 20)),
            full_name=name,
            date_of_birth=date(2009 + i % 8, (i * 5) % 12 + 1, (i * 3) % 27 + 1),
            gender=gender,
            blood_group=list(BloodGroup)[(i + 3) % 8],
            phone=f"98471{i:05d}",
            email=f"{name.split()[0].lower()}{i}@example.com",
            address=f"{120 + i}, Sports Enclave, Kozhikode, Kerala - 673001",
            category_id=categories[i % len(categories)].id,
            program_type_id=program_types[i % len(program_types)].id,
            training_center_id=centers[i % len(centers)].id,
            batch=[Batch.MORNING, Batch.EVENING, Batch.WEEKEND][i % 3],
            monthly_fee=Decimal((2000, 2500, 3000)[i % 3]),
            parent_name=parent,
            parent_relationship=Relationship.FATHER if i % 4 else Relationship.MOTHER,
            parent_phone=f"94471{i:05d}",
            parent_email=f"{parent.split()[0].lower()}.parent{i}@example.com",
            emergency_name=parent,
            emergency_relationship="Parent",
            emergency_phone=f"94471{i:05d}",
            status=RecordStatus.INACTIVE if i == 7 else RecordStatus.ACTIVE,
        )
        db.add(student)
    await db.flush()
    # Give the original seed students some history too (development data only).
    await db.execute(
        update(Student)
        .where(Student.admission_date >= add_months(month_start(today), -2))
        .values(admission_date=add_months(month_start(today), -HISTORY_MONTHS))
    )
    stats["students_added"] = len(EXTRA_STUDENTS)

    # --- fee ledger for the last months + payments ---------------------------------------------
    for back in range(HISTORY_MONTHS, -1, -1):
        await db.execute(ledger.generate_month_stmt(add_months(month_start(today), -back)))
    await db.flush()
    students = list(await db.scalars(select(Student).order_by(Student.student_code)))
    payments = 0
    for idx, student in enumerate(students):
        entries = list(
            await db.scalars(
                select(FeeLedgerEntry)
                .where(FeeLedgerEntry.student_id == student.id)
                .order_by(FeeLedgerEntry.period)
            )
        )
        profile = idx % 6  # 0-2 always pay, 3 pays late/partially, 4 is behind, 5 gets a discount
        for entry in entries:
            is_current = entry.period == month_start(today)
            if profile == 4 and entry.period >= add_months(month_start(today), -2):
                continue  # overdue for recent months
            if is_current and idx % 3 == 0:
                continue  # some current-month dues still pending
            outstanding = entry.amount_due - entry.discount - entry.amount_paid
            if outstanding <= 0:
                continue  # already settled (for example by earlier manual testing)
            amount = outstanding
            discount = None
            if profile == 5 and entry.period.month % 2 == 0 and outstanding > 500:
                discount = DiscountIn(
                    year=entry.period.year,
                    month=entry.period.month,
                    amount=Decimal(500),
                    reason="Sibling concession",
                )
                amount -= Decimal(500)
            if profile == 3:
                amount = (amount / 2).quantize(Decimal("1"))
            paid_on = min(entry.period + timedelta(days=rng.randint(2, 12)), today)
            await fees.record_payment(
                db,
                student_id=student.id,
                paid_on=paid_on,
                mode=rng.choice(
                    [PaymentMode.UPI, PaymentMode.UPI, PaymentMode.CASH, PaymentMode.BANK_TRANSFER]
                ),
                allocations=[AllocationIn(year=entry.period.year, month=entry.period.month, amount=amount)],
                discount=discount,
                reference=f"UPI/{rng.randint(10**9, 10**10 - 1)}" if rng.random() < 0.6 else None,
                remarks=f"Fee for {_month_label(entry.period)}",
                actor=admin,
            )
            payments += 1
    stats["payments"] = payments

    # --- training sessions with attendance -------------------------------------------------
    coach_categories = {c.id: [cat.id for cat in c.categories] for c in coaches}
    taken = {
        (cid, d)
        for cid, d in (await db.execute(select(SessionCoach.coach_id, SessionCoach.session_date))).all()
    }
    roster = {
        cat_id: [s for s in students if s.category_id == cat_id and s.status is RecordStatus.ACTIVE]
        for cat_id in {c for cats in coach_categories.values() for c in cats}
    }
    sessions = 0
    for back in range(SESSION_DAYS, 0, -1):
        day = today - timedelta(days=back)
        if day.weekday() == 6:  # Sunday off
            continue
        for coach in coaches:
            if (coach.id, day) in taken or not coach_categories[coach.id]:
                continue
            topic = TOPICS[(back + coaches.index(coach)) % len(TOPICS)]
            start = time(6, 0) if day.weekday() % 2 == 0 else time(16, 0)
            session = TrainingSession(
                session_date=day,
                created_by_coach_id=coach.id,
                venue=rng.choice(VENUES),
                start_time=start,
                end_time=time(start.hour + 2, 0),
                weekly_topic=topic[0],
                daily_topic=topic[1],
                explanation=f"Focus on {topic[1].lower()} under game intensity.",
                overview=rng.choice(
                    [
                        "Excellent energy and discipline throughout the session.",
                        "Good intensity; passing accuracy improved in the second half.",
                        "Solid session. A few players need work on defensive transitions.",
                    ]
                ),
            )
            session.categories = [c for c in categories if c.id in coach_categories[coach.id]]
            db.add(session)
            await db.flush()
            db.add(
                SessionCoach(
                    session_id=session.id,
                    coach_id=coach.id,
                    session_date=day,
                    role=SessionCoachRole.CREATOR,
                    status=AttendanceStatus.PRESENT,
                )
            )
            for pos, (heading, minutes, text) in enumerate(SPLITS, start=1):
                db.add(
                    SessionSplit(
                        session_id=session.id,
                        position=pos,
                        heading=heading,
                        duration_minutes=minutes,
                        explanation=text,
                    )
                )
            for cat_id in coach_categories[coach.id]:
                for s in roster.get(cat_id, []):
                    if s.admission_date > day:
                        continue
                    roll = rng.random()
                    status = (
                        AttendanceStatus.PRESENT
                        if roll < 0.84
                        else AttendanceStatus.ABSENT
                        if roll < 0.94
                        else AttendanceStatus.INFORMED
                    )
                    remarks = {
                        AttendanceStatus.ABSENT: "Unexcused",
                        AttendanceStatus.INFORMED: "Medical leave",
                    }
                    db.add(
                        StudentAttendance(
                            session_id=session.id,
                            student_id=s.id,
                            session_date=day,
                            status=status,
                            remarks=remarks.get(status),
                        )
                    )
            sessions += 1
    await db.flush()
    stats["sessions"] = sessions

    # --- performance reports (last two months) --------------------------------------------
    reports = 0
    for months_back in (2, 1):
        period = add_months(month_start(today), -months_back)
        label = f"Monthly Evaluation - {period.strftime('%b %Y')}"
        for coach in coaches:
            for cat_id in coach_categories[coach.id]:
                for s in roster.get(cat_id, [])[:4]:
                    base = rng.randint(3, 4)
                    report = PerformanceReport(
                        student_id=s.id,
                        coach_id=coach.id,
                        report_period=label,
                        recorded_date=period + timedelta(days=24),
                        position=rng.choice(POSITIONS),
                        strong_foot=rng.choice(list(StrongFoot)),
                        strengths=(
                            "1. Good first touch and composure.\n2. High work rate.\n3. Coachable attitude."
                        ),
                        areas_for_improvement=(
                            "1. Weak-foot passing under pressure.\n2. Defensive transitions."
                        ),
                        development_goals=rng.sample(STANDARD_GOALS, 3),
                        custom_goal="Improve pass completion in match play.",
                        coach_remarks=f"{s.full_name.split()[0]} made steady progress this month.",
                        overall_rating=min(5, base + (months_back == 1)),
                    )
                    report.skills = [
                        PerformanceSkillRating(
                            skill=sk, rating=max(1, min(5, base + rng.randint(-1, 1))), comment=None
                        )
                        for sk in Skill
                    ]
                    db.add(report)
                    reports += 1
    stats["performance_reports"] = reports

    # --- website: gallery, applications, enquiries ---------------------------------------
    for i, (title, category, color) in enumerate(GALLERY):
        data = _image(title, color)
        image = await files.store_bytes(
            db,
            data,
            purpose=FilePurpose.GALLERY_IMAGE,
            original_filename=f"gallery-{i + 1}.jpg",
            uploaded_by_id=admin.id,
        )
        thumb = await files.store_bytes(
            db,
            data,
            purpose=FilePurpose.GALLERY_THUMBNAIL,
            original_filename=f"gallery-{i + 1}.jpg",
            uploaded_by_id=admin.id,
        )
        db.add(
            GalleryItem(
                title=title,
                caption=title,
                category=category,
                is_wide=i % 4 == 0,
                image_file_id=image.id,
                thumbnail_file_id=thumb.id,
                sort_order=i,
            )
        )
    stats["gallery_items"] = len(GALLERY)

    jobs = {j.title: j for j in await db.scalars(select(Job))}
    for name, email, phone, city, position, app_status in APPLICANTS:
        if position not in jobs:
            continue
        cv = await files.store_bytes(
            db,
            PDF,
            purpose=FilePurpose.RESUME,
            original_filename=f"{name.split()[-1].lower()}_cv.pdf",
            uploaded_by_id=None,
        )
        db.add(
            JobApplication(
                job_id=jobs[position].id,
                full_name=name,
                email=email,
                phone=phone,
                location=city,
                cv_file_id=cv.id,
                status=app_status,
            )
        )
    stats["job_applications"] = len(APPLICANTS)

    programmes = list(await db.scalars(select(Programme).order_by(Programme.sort_order)))
    for parent, player, phone, enquiry_email, prog_idx, message, enquiry_status in ENQUIRIES:
        prog = programmes[prog_idx] if prog_idx < len(programmes) else None
        db.add(
            Enquiry(
                parent_name=parent,
                player_name=player,
                phone=phone,
                email=enquiry_email,
                programme_id=prog.id if prog else None,
                subject=f"{prog.title} ({prog.age_group})" if prog else None,
                message=message,
                status=enquiry_status,
            )
        )
    stats["enquiries"] = len(ENQUIRIES)

    # --- upcoming events ------------------------------------------------------------------
    if not await db.scalar(select(Event.id).limit(1)):
        for i, (t, k, dt, p, u) in enumerate(EVENTS):
            db.add(
                Event(
                    title=t,
                    kind=k,
                    date=dt,
                    place=p,
                    registration_url=u,
                    sort_order=i,
                )
            )
    stats["events"] = len(EVENTS)

    # --- parent payment submissions -------------------------------------------------------
    pending_students = [s for s in students if s.status is RecordStatus.ACTIVE][:4]
    for i, s in enumerate(pending_students):
        method = SubmissionMethod.UPI if i % 2 == 0 else SubmissionMethod.CASH
        screenshot = None
        if method is SubmissionMethod.UPI:
            screenshot = await files.store_bytes(
                db,
                _image(f"GPay receipt {s.full_name}", (245, 245, 245), (720, 1280)),
                purpose=FilePurpose.PAYMENT_SCREENSHOT,
                original_filename="payment.jpg",
                uploaded_by_id=None,
            )
        sub_status = SubmissionStatus.REJECTED if i == 3 else SubmissionStatus.PENDING
        db.add(
            PaymentSubmission(
                submission_number=await next_code(db, "submission", today.year, "SUB-{year}-{n:05d}"),
                student_name=s.full_name,
                parent_mobile=s.parent_phone,
                amount=s.monthly_fee,
                payment_date=today - timedelta(days=i + 1),
                payment_method=method,
                transaction_reference=f"UPI/{rng.randint(10**9, 10**10 - 1)}/PAY"
                if method is SubmissionMethod.UPI
                else None,
                handed_over_to="Academy front office" if method is SubmissionMethod.CASH else None,
                screenshot_file_id=screenshot.id if screenshot else None,
                status=sub_status,
                rejection_reason="Transaction ID not found in bank statement"
                if sub_status is SubmissionStatus.REJECTED
                else None,
                reviewed_by_id=admin.id if sub_status is SubmissionStatus.REJECTED else None,
            )
        )
    stats["payment_submissions"] = len(pending_students)

    # --- documents ---------------------------------------------------------------------------
    for s in students[:5]:
        doc = await files.store_bytes(
            db,
            PDF,
            purpose=FilePurpose.STUDENT_DOCUMENT,
            original_filename=f"birth_certificate_{s.student_code}.pdf",
            uploaded_by_id=admin.id,
        )
        db.add(
            StudentDocument(
                student_id=s.id, file_id=doc.id, title="Birth Certificate", uploaded_by_id=admin.id
            )
        )
    stats["student_documents"] = 5

    # --- notifications for the admin bell ---------------------------------------------------
    for type_, title, message, link in (
        (
            NotificationType.PAYMENT,
            "New payment submitted",
            f"{pending_students[0].full_name}: ₹{pending_students[0].monthly_fee:,.0f} via UPI.",
            "/super-admin/payments",
        ),
        (
            NotificationType.ENQUIRY,
            "New website enquiry",
            "Rahul Menon about Grassroots Kids Football.",
            "/super-admin/website/enquiries",
        ),
        (
            NotificationType.APPLICATION,
            "New job application",
            "Vikram Sethi applied for Academy Head Coach.",
            "/super-admin/website/job-applications",
        ),
        (
            NotificationType.FEE_DUE,
            "Overdue fees",
            "Several students have overdue fees.",
            "/super-admin/fees",
        ),
    ):
        await notifications.notify_admins(db, type_, title, message, link=link)

    await db.commit()
    stats["total_students"] = await db.scalar(select(func.count()).select_from(Student)) or 0
    return stats
