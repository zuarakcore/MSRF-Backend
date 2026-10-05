"""AI-generated sample images for development data (students, coaches, team, gallery, default image).

Images come from Pollinations (free text-to-image API; only the prompt text is sent). They are cached in
`sample_images/` so re-runs do not download again. Free-tier images carry a small "pollinations.ai"
mark in the bottom-right corner; it is left in place (attribution), and a Pollinations account token
removes it legitimately.

Run:  python -m app.cli attach-sample-images        (development only)
"""

import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import Gender
from app.modules.coaches.models import CoachProfile
from app.modules.files import service as files
from app.modules.files.models import FilePurpose
from app.modules.students.models import Student
from app.modules.website.models import GalleryItem, TeamMember

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BACKEND_DIR / "sample_images"
DEFAULT_IMAGE_PATH = Path(__file__).resolve().parent / "static" / "default-image.png"
API = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&seed={seed}&nologo=true&model=flux"
STYLE = "photorealistic, natural light, high detail, sharp focus"

GALLERY_PROMPTS = {
    "Match day squad": (
        "youth football team in green jerseys posing for a team photo on a stadium pitch, Kerala"
    ),
    "U-15 State Championship final": (
        "teenage football players celebrating a goal in green kits during a final match"
    ),
    "Grassroots session": (
        "young children practising football dribbling between cones on a green pitch with a coach"
    ),
    "Small-sided games": "kids playing a small-sided football match on artificial turf, action shot",
    "Academy open trials": "teenage football trial day, players in bibs lined up while coaches take notes",
    "Annual awards night": (
        "football academy awards ceremony on stage, young players holding trophies, warm lights"
    ),
    "Argentinos Juniors delegation visit": (
        "Argentine football coaches in red and white tracksuits meeting young Indian players on a pitch"
    ),
    "Coach clinic with AJ staff": (
        "football coaching clinic, coaches gathered around a tactics board on a pitch"
    ),
}


def _download(prompt: str, seed: int, size: tuple[int, int], cache_name: str) -> bytes:
    """Fetch one image (cached on disk). Retries politely: the free tier allows one request at a time."""
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / cache_name
    if cached.exists() and cached.stat().st_size > 1000:
        return cached.read_bytes()
    url = API.format(prompt=quote(f"{prompt}, {STYLE}"), w=size[0], h=size[1], seed=seed)
    for attempt in range(5):
        try:
            response = httpx.get(url, timeout=120, follow_redirects=True)
            if response.status_code == 200 and response.headers.get("content-type", "").startswith("image/"):
                cached.write_bytes(response.content)
                return response.content
            logger.warning("Image service returned %s", response.status_code)
        except httpx.HTTPError as exc:
            logger.warning("Image download failed: %s", exc)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Could not generate image for: {prompt}")


def _portrait_prompt(gender: Gender, role: str) -> str:
    who = {
        "student": {
            Gender.FEMALE: "a smiling Indian teenage girl",
            Gender.MALE: "a smiling Indian teenage boy",
        },
        "coach": {
            Gender.FEMALE: "a confident Indian woman football coach in her thirties",
            Gender.MALE: "a confident Indian man football coach in his forties",
        },
        "board": {
            Gender.FEMALE: "a distinguished senior Indian woman in formal attire",
            Gender.MALE: "a distinguished senior Indian man in a dark suit",
        },
    }[role]
    person = who.get(gender, who[Gender.MALE])
    setting = {
        "student": (
            "wearing a green football academy jersey, head and shoulders portrait, football pitch background"
        ),
        "coach": "wearing a green academy tracksuit, head and shoulders portrait, stadium background",
        "board": "professional corporate headshot, plain neutral studio background",
    }[role]
    return f"{person}, {setting}"


async def attach_sample_images(db: AsyncSession, *, progress: bool = True) -> dict[str, int]:
    if get_settings().is_production:
        raise RuntimeError("Refusing to attach sample images in production")
    stats = {"default_image": 0, "coaches": 0, "team_members": 0, "students": 0, "gallery": 0}

    def say(msg: str) -> None:
        if progress:
            print(msg, flush=True)

    # 1. The shared default image (used wherever a record still has no photo).
    say("default image ...")
    data = _download(
        "a single football resting on the centre spot of an empty floodlit green football pitch at dusk, "
        "minimal composition, no people",
        11,
        (800, 800),
        "default.jpg",
    )
    from io import BytesIO

    from PIL import Image

    Image.open(BytesIO(data)).convert("RGB").save(DEFAULT_IMAGE_PATH, format="PNG", optimize=True)
    stats["default_image"] = 1

    # 2. Coaches
    for i, coach in enumerate(
        await db.scalars(select(CoachProfile).where(CoachProfile.photo_file_id.is_(None)))
    ):
        say(f"coach: {coach.user.full_name}")
        img = _download(
            _portrait_prompt(coach.gender or Gender.MALE, "coach"), 100 + i, (640, 640), f"coach-{i}.jpg"
        )
        stored = await files.store_bytes(
            db, img, purpose=FilePurpose.COACH_PHOTO, original_filename=f"coach-{i}.jpg", uploaded_by_id=None
        )
        coach.photo_file_id = stored.id
        stats["coaches"] += 1
    await db.commit()

    # 3. Team / board members (names in the seed are men)
    for i, member in enumerate(
        await db.scalars(select(TeamMember).where(TeamMember.photo_file_id.is_(None)))
    ):
        say(f"team member: {member.name}")
        img = _download(_portrait_prompt(Gender.MALE, "board"), 200 + i, (640, 640), f"team-{i}.jpg")
        stored = await files.store_bytes(
            db, img, purpose=FilePurpose.TEAM_PHOTO, original_filename=f"team-{i}.jpg", uploaded_by_id=None
        )
        member.photo_file_id = stored.id
        stats["team_members"] += 1
    await db.commit()

    # 4. Students (fictional AI portraits; commit as we go so an interruption keeps progress)
    students = list(
        await db.scalars(
            select(Student).where(Student.photo_file_id.is_(None)).order_by(Student.student_code)
        )
    )
    for i, student in enumerate(students):
        say(f"student {i + 1}/{len(students)}: {student.full_name}")
        img = _download(
            _portrait_prompt(student.gender, "student"),
            300 + i,
            (512, 512),
            f"student-{student.student_code}.jpg",
        )
        stored = await files.store_bytes(
            db,
            img,
            purpose=FilePurpose.STUDENT_PHOTO,
            original_filename=f"{student.student_code}.jpg",
            uploaded_by_id=None,
        )
        student.photo_file_id = stored.id
        stats["students"] += 1
        await db.commit()

    # 5. Gallery: replace the generated placeholder pictures with AI photos
    for i, item in enumerate(await db.scalars(select(GalleryItem).order_by(GalleryItem.sort_order))):
        prompt = GALLERY_PROMPTS.get(item.title)
        if prompt is None or not item.image.original_filename.startswith("gallery-"):
            continue  # only demo items; never touch real uploads
        say(f"gallery: {item.title}")
        img = _download(prompt, 400 + i, (1280, 800), f"gallery-ai-{i}.jpg")
        old = [item.image, item.thumbnail]
        image = await files.store_bytes(
            db,
            img,
            purpose=FilePurpose.GALLERY_IMAGE,
            original_filename=f"ai-gallery-{i}.jpg",
            uploaded_by_id=None,
        )
        thumb = await files.store_bytes(
            db,
            img,
            purpose=FilePurpose.GALLERY_THUMBNAIL,
            original_filename=f"ai-gallery-{i}.jpg",
            uploaded_by_id=None,
        )
        item.image_file_id, item.thumbnail_file_id = image.id, thumb.id
        await db.flush()
        pending = [await files.delete_stored_file(db, f) for f in old if f is not None]
        await db.commit()
        await files.purge_objects(pending)
        stats["gallery"] += 1
    return stats
