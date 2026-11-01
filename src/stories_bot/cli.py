"""Command-line entry point: publish the next Story in the queue if the pace allows it."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ConfigError, InstagramSettings, R2Settings
from .instagram import InstagramClient, InstagramError
from .notify import TelegramNotifier
from .pace import PaceError, load_pace, may_publish
from .state import load_posted, record_post
from .storage import R2Storage, Storage, StorageError
from .story_queue import load_queue, record_attempt, remove_item

log = logging.getLogger("stories_bot")

EXIT_OK, EXIT_FAILED, EXIT_BAD_SETUP = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stories-bot", description="Publish the next queued Instagram Story."
    )
    parser.add_argument("--pace", type=Path, default=Path("pace.json"))
    parser.add_argument("--queue", type=Path, default=Path("state/queue.json"))
    parser.add_argument("--failed", type=Path, default=Path("state/failed.json"))
    parser.add_argument("--state", type=Path, default=Path("state/posted.json"))
    parser.add_argument("--dry-run", action="store_true", help="show what would be published")
    parser.add_argument("--now", help="pretend it is this local time, e.g. 2026-10-09T18:05")
    return parser


def resolve_now(value: str | None, tz: ZoneInfo) -> datetime:
    if value:
        return datetime.fromisoformat(value).replace(tzinfo=tz)
    return datetime.now(tz)


def build_storage(settings: R2Settings) -> Storage:
    return R2Storage(settings)


def _notify(notifier: TelegramNotifier | None, text: str) -> None:
    if notifier is not None:
        notifier.send(text)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        pace = load_pace(args.pace)
    except (OSError, PaceError) as exc:
        log.error("Cannot load pace %s: %s", args.pace, exc)
        return EXIT_BAD_SETUP

    now = resolve_now(args.now, pace.timezone)
    posted = load_posted(args.state)

    # An item recorded as posted but still queued means a run died between the two writes.
    for stale in [i for i in load_queue(args.queue) if i.id in posted]:
        log.warning("%s was already posted; removing it from the queue", stale.name)
        remove_item(args.queue, stale.id)

    queue = load_queue(args.queue)
    if not queue:
        log.info("Queue is empty")
        return EXIT_OK
    if not may_publish(pace, now, posted):
        log.info(
            "%s: pace does not allow a Story right now (%d queued)", f"{now:%H:%M}", len(queue)
        )
        return EXIT_OK

    item = queue[0]
    if args.dry_run:
        log.info("[dry-run] would publish %s (%d queued)", item.name, len(queue))
        return EXIT_OK

    notifier = TelegramNotifier.from_env()
    try:
        instagram = InstagramSettings.from_env()
        storage = build_storage(R2Settings.from_env())
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        _notify(notifier, f"❌ Story não publicado: configuração incompleta ({exc}).")
        return EXIT_BAD_SETUP

    client = InstagramClient(instagram.user_id, instagram.access_token)
    try:
        media_id = client.publish_story(storage.public_url(item.key), is_video=item.is_video)
    except InstagramError as exc:
        log.error("Failed to publish %s: %s", item.name, exc)
        if exc.is_auth_error:  # every Story would fail the same way: do not burn attempts
            _notify(notifier, "❌ O token do Instagram expirou. Gere um novo (stories-token).")
            return EXIT_BAD_SETUP
        gave_up = record_attempt(args.queue, args.failed, item.id, str(exc))
        suffix = " Desisti dele e segui a fila." if gave_up else " Tento de novo na próxima rodada."
        _notify(notifier, f"❌ Falha ao publicar {item.name}: {exc}.{suffix}")
        return EXIT_FAILED

    record_post(args.state, item.id, item.name, media_id, now)
    remove_item(args.queue, item.id)
    log.info("Published %s (media id %s)", item.name, media_id)
    try:
        storage.delete(item.key)  # Meta already has its copy; keep the bucket small
    except StorageError as exc:
        log.warning("Could not delete %s from storage: %s", item.key, exc)

    remaining = len(queue) - 1
    if pace.notify_each:
        _notify(notifier, f"✅ Story publicado: {item.name} ({now:%d/%m %H:%M}). Fila: {remaining}")
    if remaining == 0:
        _notify(notifier, "⚠️ A fila de Stories acabou. Mande mais artes para o bot.")
    elif remaining == pace.low_queue:
        _notify(notifier, f"⚠️ Fila acabando: restam {remaining} Stories.")
    return EXIT_OK
