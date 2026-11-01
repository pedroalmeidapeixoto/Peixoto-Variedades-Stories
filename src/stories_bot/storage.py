"""Where the arts live: a Cloudflare R2 bucket Meta can download from.

The repository holds only the queue; keeping dozens of arts a day out of git keeps it small,
and a published art can be deleted from the bucket right away.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import R2Settings

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


class StorageError(RuntimeError):
    """The bucket rejected a request (credentials are never part of the message)."""


class Storage(Protocol):
    def upload(self, key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def public_url(self, key: str) -> str: ...


class R2Storage:
    def __init__(self, settings: R2Settings, *, client: Any | None = None) -> None:
        self._bucket = settings.bucket
        self._public_url = settings.public_url
        self._client = client or boto3.client(
            "s3",
            endpoint_url=f"https://{settings.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name="auto",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def upload(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"upload of {key} failed: {type(exc).__name__}") from None

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"delete of {key} failed: {type(exc).__name__}") from None

    def public_url(self, key: str) -> str:
        return f"{self._public_url}/{quote(key)}"
