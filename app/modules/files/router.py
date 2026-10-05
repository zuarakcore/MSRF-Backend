import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AdminUser, DbSession
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation, NotFound
from app.core.rate_limit import RateLimit
from app.core.storage import LocalStorage, get_storage, verify_file_link
from app.modules.files import service
from app.modules.files.models import GENERIC_UPLOAD_PURPOSES, FilePurpose, StoredFile
from app.modules.files.schemas import FileRef

router = APIRouter(tags=["Files"])


@router.post(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    response_model=FileRef,
    dependencies=[Depends(RateLimit("uploads", "30/minute"))],
    summary="Upload a photo; send the returned id as photoFileId in a create/update body",
)
async def upload(
    admin: AdminUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[FilePurpose, Form()],
) -> FileRef:
    if purpose not in GENERIC_UPLOAD_PURPOSES:
        raise BusinessRuleViolation("This upload type uses its own endpoint", code="INVALID_PURPOSE")
    stored = await service.store_upload(db, file, purpose=purpose, uploaded_by_id=admin.id)
    await db.commit()
    return FileRef.model_validate(stored)


@router.get(
    "/files/{file_id}/content",
    summary="Download a private file through a signed link (local storage only)",
    response_class=FileResponse,
)
async def file_content(
    file_id: uuid.UUID,
    db: DbSession,
    expires: Annotated[int, Query()],
    signature: Annotated[str, Query(max_length=128)],
    inline: Annotated[int, Query(ge=0, le=1)] = 0,
) -> FileResponse:
    """The signature is the authorization: links are only issued in responses the caller may see."""
    storage = get_storage()
    if not isinstance(storage, LocalStorage) or not verify_file_link(
        str(file_id), expires, bool(inline), signature
    ):
        raise NotFound("File not found or link expired", code="FILE_NOT_FOUND")
    stored = await db.get(StoredFile, file_id)
    if stored is None:
        raise NotFound("File not found or link expired", code="FILE_NOT_FOUND")
    path = storage.path(stored.storage_key, stored.visibility)
    if not path.is_file():
        raise NotFound("File not found or link expired", code="FILE_NOT_FOUND")

    disposition = "inline" if inline else "attachment"
    return FileResponse(
        path,
        media_type=stored.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(stored.original_filename)}",
            "Cache-Control": f"private, max-age={get_settings().FILE_URL_TTL_SECONDS}",
        },
    )
