"""Persistent records (keep the bot idempotent across runs): what went out, what failed."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_posted(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def write_json(path: Path, data: Any) -> None:
    """Write atomically (a crash never leaves the file half-written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def record_post(path: Path, item_id: str, name: str, media_id: str, at: datetime) -> None:
    posted = load_posted(path)
    posted[item_id] = {"name": name, "media_id": media_id, "posted_at": at.isoformat()}
    write_json(path, posted)


def load_offset(path: Path) -> int | None:
    """Next Telegram update id to ask for, or None before the first run."""
    if not path.exists():
        return None
    return int(json.loads(path.read_text(encoding="utf-8"))["offset"])


def save_offset(path: Path, offset: int) -> None:
    write_json(path, {"offset": offset})
