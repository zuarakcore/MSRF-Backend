"""Object storage: local disk in development, any S3-compatible service in production.

PUBLIC objects (gallery, team photos) are served directly (static mount or CDN).
PRIVATE objects (student photos, documents, CVs, payment screenshots) are only reachable
through short-lived signed URLs that the API hands out in responses the caller may see.
"""

import hashlib
import hmac
import time
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from app.core.config import get_settings


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str, visibility: Visibility) -> None: ...
    def delete(self, key: str, visibility: Visibility) -> None: ...
    def private_url(self, key: str, file_id: str, filename: str, inline: bool) -> str: ...


def public_url(key: str) -> str:
    return f"{get_settings().PUBLIC_MEDIA_BASE_URL.rstrip('/')}/{quote(key)}"


def _signing_key() -> bytes:
    secret = get_settings().JWT_SECRET_KEY.get_secret_value()
    return hashlib.sha256(b"file-url:" + secret.encode()).digest()


def sign_file_link(file_id: str, expires: int, inline: bool) -> str:
    message = f"{file_id}:{expires}:{int(inline)}".encode()
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def verify_file_link(file_id: str, expires: int, inline: bool, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(sign_file_link(file_id, expires, inline), signature)


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def path(self, key: str, visibility: Visibility) -> Path:
        base = self.root / visibility.value.lower()
        target = (base / key).resolve()
        if not target.is_relative_to(base):  # keys are generated, but never trust a path join
            raise ValueError("Invalid storage key")
        return target

    def put(self, key: str, data: bytes, content_type: str, visibility: Visibility) -> None:
        target = self.path(key, visibility)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def delete(self, key: str, visibility: Visibility) -> None:
        self.path(key, visibility).unlink(missing_ok=True)

    def private_url(self, key: str, file_id: str, filename: str, inline: bool) -> str:
        settings = get_settings()
        expires = int(time.time()) + settings.FILE_URL_TTL_SECONDS
        query = urlencode(
            {
                "expires": expires,
                "inline": int(inline),
                "signature": sign_file_link(file_id, expires, inline),
            }
        )
        return f"{settings.API_BASE_URL.rstrip('/')}{settings.API_PREFIX}/files/{file_id}/content?{query}"


class S3Storage:
    def __init__(self) -> None:
        import boto3  # imported lazily: not needed in local development

        settings = get_settings()
        self.settings = settings
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value() or None,
        )

    def _bucket(self, visibility: Visibility) -> str:
        s = self.settings
        return s.S3_BUCKET_PUBLIC if visibility is Visibility.PUBLIC else s.S3_BUCKET_PRIVATE

    def put(self, key: str, data: bytes, content_type: str, visibility: Visibility) -> None:
        self.client.put_object(Bucket=self._bucket(visibility), Key=key, Body=data, ContentType=content_type)

    def delete(self, key: str, visibility: Visibility) -> None:
        self.client.delete_object(Bucket=self._bucket(visibility), Key=key)

    def private_url(self, key: str, file_id: str, filename: str, inline: bool) -> str:
        disposition = "inline" if inline else "attachment"
        url: str = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.settings.S3_BUCKET_PRIVATE,
                "Key": key,
                "ResponseContentDisposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            },
            ExpiresIn=self.settings.FILE_URL_TTL_SECONDS,
        )
        return url


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(settings.MEDIA_ROOT)
