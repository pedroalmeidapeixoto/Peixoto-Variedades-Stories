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
ROTATING_MAX_ATTEMPTS = 6  # a recycled queue is more patient: the art is part of the daily pack


@dataclass(frozen=True)
class QueueItem:
    id: str
    key: str  # object key in the storage bucket
    name: str  # original file name, shown in messages
    added_at: str
    attempts: int = 0
    last_error: str = ""  # why the latest attempt failed, so it can be read without the logs

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
    again = QueueItem(**{**asdict(item), "id": new_id, "attempts": 0, "last_error": ""})
    save_queue(path, [*(i for i in items if i.id != item_id), again])
    return True


def record_attempt(
    path: Path,
    failed_path: Path,
    item_id: str,
    error: str,
    *,
    rotate: bool = False,
    max_attempts: int = MAX_ATTEMPTS,
) -> bool:
    """Count a failed attempt. After `max_attempts` the item moves to the failed list.

    With `rotate` (a recycled queue) a failing item also goes to the back of the line, so one
    bad art never holds up the others. Returns True when the item was given up on.
    """
    items = load_queue(path)
    for index, item in enumerate(items):
        if item.id != item_id:
            continue
        attempts = item.attempts + 1
        if attempts < max_attempts:
            tried = QueueItem(**{**asdict(item), "attempts": attempts, "last_error": error[:300]})
            if rotate:
                items = [*(i for i in items if i.id != item_id), tried]
            else:
                items[index] = tried
            save_queue(path, items)
            return False
        failed = _load_failed(failed_path)
        failed.append({**asdict(item), "attempts": attempts, "error": error})
        write_json(failed_path, failed)
        save_queue(path, [i for i in items if i.id != item_id])
        return True
    return False


def requeue_failed(queue_path: Path, failed_path: Path, now: datetime) -> int:
    """Put every art in the failed list back into the queue and return how many came back.

    The whole queue is then ordered by file name (the `NN-` prefix is the posting order) and
    turned so that the art that was next stays next.
    """
    failed = _load_failed(failed_path)
    if not failed:
        return 0
    items = load_queue(queue_path)
    head = items[0].name if items else None
    seen = {i.key for i in items}
    back: list[QueueItem] = []
    for entry in failed:
        key = str(entry["key"])
        if key in seen:
            continue
        seen.add(key)
        back.append(
            QueueItem(
                id=f"{str(entry['id']).split('@')[0]}@{now:%Y%m%d%H%M%S}",
                key=key,
                name=str(entry["name"]),
                added_at=str(entry["added_at"]),
            )
        )
    merged = sorted([*items, *back], key=lambda i: i.name)
    if head is not None:
        start = next(n for n, i in enumerate(merged) if i.name == head)
        merged = merged[start:] + merged[:start]
    save_queue(queue_path, merged)
    write_json(failed_path, [])
    return len(back)


def _load_failed(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    failed: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return failed
