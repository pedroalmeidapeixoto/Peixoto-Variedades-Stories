"""The product queue: arts waiting to go out, oldest first, plus the ones that gave up.

The queue is a JSON file in the repository (`state/queue.json`); the art files themselves
live in the storage bucket under each item's `key`.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .state import write_json
from .storage import CONTENT_TYPES, Storage

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov"})
MAX_ATTEMPTS = 3  # a Story that fails this many times is set aside so it cannot block the queue


@dataclass(frozen=True)
class QueueItem:
    id: str
    key: str  # object key in the storage bucket
    name: str  # original file name, shown in messages
    added_at: str
    attempts: int = 0

    @property
    def is_video(self) -> bool:
        return Path(self.key).suffix.lower() in VIDEO_EXTENSIONS


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "arte"


def load_queue(path: Path) -> list[QueueItem]:
    if not path.exists():
        return []
    return [QueueItem(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))]


def save_queue(path: Path, items: list[QueueItem]) -> None:
    write_json(path, [asdict(item) for item in items])


def add_art(
    storage: Storage,
    queue_path: Path,
    data: bytes,
    *,
    stem: str,
    suffix: str,
    now: datetime,
    front: bool = False,
) -> QueueItem:
    """Upload an art and put it in the queue (at the back, or at the front when `front`)."""
    suffix = suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise ValueError(f"unsupported media type {suffix!r}")
    item_id = f"{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    item = QueueItem(
        id=item_id,
        key=f"stories/{item_id}-{_slug(stem)}{suffix}",
        name=f"{stem}{suffix}",
        added_at=now.isoformat(),
    )
    # Upload first: an item must never be queued without its file.
    storage.upload(item.key, data, CONTENT_TYPES[suffix])
    items = load_queue(queue_path)
    save_queue(queue_path, [item, *items] if front else [*items, item])
    return item


def remove_item(path: Path, item_id: str) -> QueueItem | None:
    """Take an item out of the queue and return it (None when it is not there)."""
    items = load_queue(path)
    removed = next((i for i in items if i.id == item_id), None)
    if removed is not None:
        save_queue(path, [i for i in items if i.id != item_id])
    return removed


def recycle_item(path: Path, item_id: str, new_id: str) -> bool:
    """Send an item to the back of the queue under a fresh id (its art stays in storage).

    The id changes because posted Stories are recorded by id: the next lap of the same art
    must not look like one that was already posted.
    """
    items = load_queue(path)
    item = next((i for i in items if i.id == item_id), None)
    if item is None:
        return False
    again = QueueItem(**{**asdict(item), "id": new_id, "attempts": 0})
    save_queue(path, [*(i for i in items if i.id != item_id), again])
    return True


def record_attempt(path: Path, failed_path: Path, item_id: str, error: str) -> bool:
    """Count a failed attempt. After MAX_ATTEMPTS the item moves to the failed list.

    Returns True when the item was given up on.
    """
    items = load_queue(path)
    for index, item in enumerate(items):
        if item.id != item_id:
            continue
        attempts = item.attempts + 1
        if attempts < MAX_ATTEMPTS:
            items[index] = QueueItem(**{**asdict(item), "attempts": attempts})
            save_queue(path, items)
            return False
        failed = _load_failed(failed_path)
        failed.append({**asdict(item), "attempts": attempts, "error": error})
        write_json(failed_path, failed)
        save_queue(path, [i for i in items if i.id != item_id])
        return True
    return False


def _load_failed(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    failed: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return failed
