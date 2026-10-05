import uuid
from typing import Any

from pydantic import model_validator

from app.core.schemas import CamelModel
from app.core.storage import Visibility, get_storage, public_url
from app.modules.files.models import StoredFile


def file_url(stored: StoredFile) -> str:
    if stored.visibility is Visibility.PUBLIC:
        return public_url(stored.storage_key)
    return get_storage().private_url(
        stored.storage_key, str(stored.id), stored.original_filename, inline=stored.is_image
    )


class FileRef(CamelModel):
    """A file as the frontend sees it. `url` is either a public CDN URL or a short-lived signed link."""

    id: uuid.UUID
    file_name: str
    content_type: str
    size_bytes: int
    url: str

    @model_validator(mode="before")
    @classmethod
    def _from_stored_file(cls, value: Any) -> Any:
        if isinstance(value, StoredFile):
            return {
                "id": value.id,
                "file_name": value.original_filename,
                "content_type": value.content_type,
                "size_bytes": value.size_bytes,
                "url": file_url(value),
            }
        return value
