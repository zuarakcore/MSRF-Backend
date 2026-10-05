"""Development seed data, modelled on the admin app's mock data (src/mock-data/msrf-data.ts).

Refuses to run in production. Idempotent: does nothing if categories already exist.
"""

import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.counters import next_code
from app.core.enums import Batch, BloodGroup, Gender, Relationship
from app.core.security import hash_password
from app.core.timeutils import today_local
from app.modules.coaches.models import CoachProfile
from app.modules.fees import ledger
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.students.models import Student
from app.modules.users.models import Role, User
from app.modules.website.models import Event, Job, JobStatus, Programme, TeamMember
from app.modules.website.service import slugify

CATEGORIES = [
    ("Grassroots Football (U-10)", "Junior grassroots ball control & fundamental football skills"),
    ("Youth Football Squad (U-13)", "Youth tactical awareness, passing accuracy & speed conditioning"),
    ("Junior Football Academy (U-15)", "Competitive 11v11 squad preparation & positional play"),
    ("Senior Pro Division (U-18)", "Advanced match tactics, physical power & state championship squad"),
]
PROGRAM_TYPES = [
    ("Day Scholar Program", "Daily morning or evening non-residential coaching sessions"),
    ("Residential Program", "Full boarding & intensive sports performance program"),
    ("Weekend Program", "Saturday & Sunday specialized coaching for school students"),
]
CENTERS = [
    ("Kozhikode Main Campus", "MSRF Sports Complex, Kozhikode, Kerala", "9847055443"),
    ("Malappuram Sports Hub", "Stadium Road, Malappuram, Kerala", "9447122334"),
    ("Calicut Stadium Annex", "Medical College Ground, Kozhikode", "9895277889"),
]
STUDENTS = [
    ("Adarsh Nair", Gender.MALE, "Ramesh Nair"),
    ("Ananya Ramesh", Gender.FEMALE, "K. Ramesh"),
    ("Rohan Kulkarni", Gender.MALE, "Sanjay Kulkarni"),
    ("Devika Menon", Gender.FEMALE, "Vijay Menon"),
    ("Mohammed Shahid", Gender.MALE, "Abdul Shahid"),
    ("Sania Kurien", Gender.FEMALE, "Mathew Kurien"),
    ("Siddharth Varma", Gender.MALE, "Gopalan Varma"),
    ("Kavya Pillai", Gender.FEMALE, "Sudhakaran Pillai"),
    ("Aarav Sharma", Gender.MALE, "Rajeev Sharma"),
    ("Diya Krishnan", Gender.FEMALE, "Unnikrishnan"),
    ("Gautam Nambiar", Gender.MALE, "Vinod Nambiar"),
    ("Meera Thomas", Gender.FEMALE, "Thomas Kurian"),
]
PROGRAMMES = [
    (
        "Grassroots Kids Football",
        "6 – 10 years",
        "A fun-first program introducing ball mastery and football fundamentals.",
        "12 months",
        "Tue · Thu · Sat",
        "MCFC grassroots staff",
        ["Ball mastery fundamentals", "Fun-first coaching", "Nutrition guidance", "Parent progress reports"],
    ),
    (
        "Youth Development Programme",
        "11 – 14 years",
        "Position-specific training with regular match exposure and video analysis.",
        "12 months",
        "Mon · Wed · Fri · Sun",
        "AJ-accredited coaches",
        ["Position-specific training", "Match exposure", "Video analysis", "Academy scouting pathway"],
    ),
    (
        "Elite Residential Training",
        "15 – 18 years",
        "Intensive training with double sessions, strength conditioning, and professional trial pathways.",
        "Season-long",
        "Six days a week",
        "MCFC technical director",
        [
            "Double sessions",
            "Strength & conditioning",
            "Argentina exposure trips",
            "Professional trial pathway",
        ],
    ),
    (
        "Goalkeeper Academy",
        "10 – 18 years",
        "Specialized training focusing on shot-stopping, distribution, and reaction drills.",
        "6 months",
        "Tue · Fri · Sun",
        "Padma Shri Bhramanand S. K. S.",
        ["Shot-stopping technique", "Distribution & sweeping", "Reaction drills", "One-to-one review"],
    ),
    (
        "Weekend Batch",
        "8 – 16 years",
        "Weekend specialized coaching for students balancing school academics.",
        "Rolling monthly",
        "Sat · Sun",
        "Academy coaching panel",
        ["School-friendly timing", "Small-sided games", "Skill assessment", "Progress badges"],
    ),
    (
        "Performance & Fitness",
        "14 years and above",
        "Advanced athletic conditioning, speed & agility protocols for competitive athletes.",
        "3 months",
        "Mon · Wed · Fri",
        "Sports science team",
        ["Speed & agility blocks", "Injury prevention", "Recovery protocols", "Body composition tracking"],
    ),
]
TEAM = [
    ("John Doe", "CHAIRMAN", "Former Chief Secretary to the Government of Goa"),
    ("Michael Smith", "DIRECTOR", "Former Additional Chief Secretary to the Government of Tamil Nadu"),
    ("Robert Johnson", "DIRECTOR", "Former Chief Secretary to the Government of Kerala"),
    (
        "William Brown",
        "MANAGING DIRECTOR & CEO",
        "Former Superintendent of Police, National Sports Administrator",
    ),
]
JOBS = [
    (
        "Academy Head Coach",
        "Kozhikode, Keralam",
        "Lead the technical development of our youth teams, implement "
        "the Argentinos Juniors methodology, and mentor junior coaching staff.",
        "5+ Years (AFC / UEFA License)",
    ),
    (
        "Sports Physiotherapist",
        "Kozhikode, Keralam",
        "Manage player health, injury prevention protocols, and "
        "rehabilitation programs for the entire academy.",
        "3+ Years",
    ),
    (
        "Youth Scout",
        "Keralam (Statewide)",
        "Identify talent across district and state level school tournaments.",
        "2+ Years",
    ),
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


async def seed(db: AsyncSession) -> list[tuple[str, str, str]]:
    """Returns (role, email, password) for the demo accounts created."""
    if get_settings().is_production:
        raise RuntimeError("Refusing to seed demo data in production")
    if await db.scalar(select(Category.id).limit(1)):
        return []

    categories = [Category(name=n, description=d, sort_order=i) for i, (n, d) in enumerate(CATEGORIES)]
    program_types = [ProgramType(name=n, description=d) for n, d in PROGRAM_TYPES]
    centers = [TrainingCenter(name=n, location=loc, phone=p) for n, loc, p in CENTERS]
    db.add_all([*categories, *program_types, *centers])
    await db.flush()

    accounts: list[tuple[str, str, str]] = []

    def account(role: Role, email: str, name: str) -> User:
        password = secrets.token_urlsafe(9)
        accounts.append((role.value, email, password))
        return User(
            email=email, full_name=name, role=role, password_hash=hash_password(password), is_verified=True
        )

    admin = account(Role.ADMIN, "admin@example.com", "MSRF Director")
    coach_users = [
        account(Role.COACH, "rajesh.varma@example.com", "Rajesh Varma"),
        account(Role.COACH, "priya.nambiar@example.com", "Priya Nambiar"),
    ]
    db.add_all([admin, *coach_users])
    await db.flush()
    coaches = [
        CoachProfile(
            user_id=coach_users[0].id,
            phone="9847012345",
            gender=Gender.MALE,
            blood_group=BloodGroup.O_POS,
            experience_years=12,
            joined_date=date(2021, 3, 15),
            bio="AFC Pro License Holder. Former national team player.",
        ),
        CoachProfile(
            user_id=coach_users[1].id,
            phone="9447123456",
            gender=Gender.FEMALE,
            blood_group=BloodGroup.A_POS,
            experience_years=8,
            joined_date=date(2022, 1, 10),
            bio="AFC Level 2 accredited. Youth tactical movement and conditioning.",
        ),
    ]
    coaches[0].categories = categories[:2]
    coaches[1].categories = categories[2:]
    db.add_all(coaches)

    today = today_local()
    for i, (name, gender, parent) in enumerate(STUDENTS):
        admission = today.replace(day=1) - timedelta(days=30 * (i % 3))
        student = Student(
            student_code=await next_code(db, "student", admission.year, "MSRF-{year}-{n:03d}"),
            admission_number=await next_code(db, "admission", admission.year, "ADM-{year}-{n:03d}"),
            admission_date=admission,
            full_name=name,
            date_of_birth=date(2010 + i % 6, (i % 12) + 1, 15),
            gender=gender,
            blood_group=list(BloodGroup)[i % 8],
            phone=f"98470{i:05d}",
            category_id=categories[i % 4].id,
            program_type_id=program_types[i % 3].id,
            training_center_id=centers[i % 3].id,
            batch=Batch.MORNING if i % 2 == 0 else Batch.EVENING,
            monthly_fee=Decimal((2000, 2500, 3000)[i % 3]),
            parent_name=parent,
            parent_relationship=Relationship.FATHER,
            parent_phone=f"94470{i:05d}",
            emergency_name=parent,
            emergency_phone=f"94470{i:05d}",
        )
        db.add(student)
        await db.flush()
        await ledger.open_first_entry(db, student)

    db.add_all(
        Programme(
            slug=slugify(t),
            title=t,
            age_group=a,
            description=d,
            duration=du,
            training_days=days,
            coach_label=c,
            benefits=b,
            sort_order=i,
        )
        for i, (t, a, d, du, days, c, b) in enumerate(PROGRAMMES)
    )
    db.add_all(
        TeamMember(name=n, designation=d, biography=b, sort_order=i) for i, (n, d, b) in enumerate(TEAM)
    )
    db.add_all(
        Job(
            title=t,
            location=loc,
            description=d,
            experience_required=e,
            posted_on=today - timedelta(days=10),
            status=JobStatus.OPEN,
        )
        for t, loc, d, e in JOBS
    )
    db.add_all(
        Event(
            title=t,
            kind=k,
            date=dt,
            place=p,
            registration_url=u,
            sort_order=i,
        )
        for i, (t, k, dt, p, u) in enumerate(EVENTS)
    )
    await db.commit()
    return accounts
