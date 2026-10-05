"""Upload validation and storage.

Pipeline: bounded read -> magic-byte type detection -> per-purpose allowlist ->
images re-encoded with Pillow (strips EXIF/GPS, defeats polyglot files, caps size) ->
generated storage key -> StoredFile row.
"""

import hashlib
import io
import logging
import re
import unicodedata
import uuid
from datetime import date

import filetype
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError, BusinessRuleViolation
from app.core.storage import Visibility, get_storage
from app.modules.files.models import UPLOAD_POLICIES, FilePurpose, StoredFile

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 40_000_000  # ~6300 x 6300; larger images are refused before decoding

_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
_PIL_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class FileTooLarge(AppError):
    status_code = 413
    default_code = "FILE_TOO_LARGE"
    default_detail = "File is too large"


class UnsupportedFileType(AppError):
    status_code = 415
    default_code = "UNSUPPORTED_FILE_TYPE"
    default_detail = "This file type is not allowed"


def sanitize_filename(name: str | None, fallback_ext: str) -> str:
    """Only used for Content-Disposition; never part of a storage path."""
    base = unicodedata.normalize("NFKC", (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip(" ._")[:150]
    return base or f"file.{fallback_ext}"


async def read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise FileTooLarge(f"File must be {max_bytes // (1024 * 1024)} MB or smaller")
        chunks.append(chunk)
    if total == 0:
        raise UnsupportedFileType("The file is empty", code="EMPTY_FILE")
    return b"".join(chunks)


def _process_image(data: bytes, content_type: str, max_px: int) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            # Dimensions come from the header; reject decompression bombs before decoding pixels.
            # (Pillow itself only *warns* until twice its limit.)
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise UnsupportedFileType("Image dimensions are too large", code="IMAGE_TOO_LARGE")
            source.load()
            img: Image.Image = ImageOps.exif_transpose(source)  # keep orientation, drop all metadata
            if content_type == "image/jpeg" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((max_px, max_px))
            out = io.BytesIO()
            params = {"quality": 85, "optimize": True} if content_type != "image/png" else {"optimize": True}
            img.save(out, format=_PIL_FORMATS[content_type], **params)
            return out.getvalue(), img.width, img.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise UnsupportedFileType("The image could not be read") from exc


async def store_bytes(
    db: AsyncSession,
    data: bytes,
    *,
    purpose: FilePurpose,
    original_filename: str | None,
    uploaded_by_id: uuid.UUID | None,
) -> StoredFile:
    policy = UPLOAD_POLICIES[purpose]
    if len(data) > policy.max_bytes:
        raise FileTooLarge(f"File must be {policy.max_bytes // (1024 * 1024)} MB or smaller")

    kind = filetype.guess(data)
    content_type = kind.mime if kind else "application/octet-stream"
    if content_type not in policy.allowed_types:
        raise UnsupportedFileType()

    width = height = None
    if content_type.startswith("image/"):
        data, width, height = await run_in_threadpool(_process_image, data, content_type, policy.max_image_px)

    ext = _EXTENSIONS[content_type]
    today = date.today()
    file_id = uuid.uuid4()
    key = f"{purpose.value.lower()}/{today:%Y/%m}/{file_id.hex}.{ext}"
    await run_in_threadpool(get_storage().put, key, data, content_type, policy.visibility)

    stored = StoredFile(
        id=file_id,
        storage_key=key,
        visibility=policy.visibility,
        purpose=purpose,
        original_filename=sanitize_filename(original_filename, ext),
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(stored)
    await db.flush()
    return stored


async def store_upload(
    db: AsyncSession, upload: UploadFile, *, purpose: FilePurpose, uploaded_by_id: uuid.UUID | None
) -> StoredFile:
    data = await read_limited(upload, UPLOAD_POLICIES[purpose].max_bytes)
    return await store_bytes(
        db, data, purpose=purpose, original_filename=upload.filename, uploaded_by_id=uploaded_by_id
    )


async def get_file_of_purpose(
    db: AsyncSession, file_id: uuid.UUID | None, purpose: FilePurpose
) -> StoredFile | None:
    """Resolve a file id sent in a JSON body; it must exist and have been uploaded for this purpose."""
    if file_id is None:
        return None
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.purpose is not purpose:
        raise BusinessRuleViolation("Uploaded file not found for this field", code="FILE_NOT_FOUND")
    return stored


PendingDelete = tuple[str, Visibility]


async def delete_stored_file(db: AsyncSession, stored: StoredFile) -> PendingDelete:
    """Delete the row in the current transaction. Pass the result to `purge_objects` AFTER commit,
    so a rolled-back transaction never loses the underlying file."""
    await db.delete(stored)
    return stored.storage_key, stored.visibility


async def purge_objects(pending: list[PendingDelete]) -> None:
    for key, visibility in pending:
        try:
            await run_in_threadpool(get_storage().delete, key, visibility)
        except Exception as exc:  # an orphaned object is harmless; a failed request is not
            logger.warning("Could not delete stored object", extra={"key": key, "error": str(exc)})
