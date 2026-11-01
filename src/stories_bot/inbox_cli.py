"""Command-line entry point: receive arts sent to the Telegram bot and queue them."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from .cli import EXIT_BAD_SETUP, EXIT_FAILED, EXIT_OK, build_storage, resolve_now
from .config import ConfigError, R2Settings
from .inbox import Inbox, InboxPaths
from .pace import PaceError, load_pace
from .telegram import TelegramApi, TelegramError

log = logging.getLogger("stories_bot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stories-inbox", description="Receive Story arts sent to the Telegram bot."
    )
    parser.add_argument("--pace", type=Path, default=Path("pace.json"))
    parser.add_argument("--queue", type=Path, default=Path("state/queue.json"))
    parser.add_argument("--offset-file", type=Path, default=Path("state/telegram.json"))
    parser.add_argument("--now", help="pretend it is this local time, e.g. 2026-10-09T18:05")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token, chat_id = (
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    if not token or not chat_id:
        log.info("Telegram is not configured; nothing to receive")
        return EXIT_OK

    try:
        pace = load_pace(args.pace)
        storage = build_storage(R2Settings.from_env())
    except (OSError, PaceError) as exc:
        log.error("Cannot load pace %s: %s", args.pace, exc)
        return EXIT_BAD_SETUP
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EXIT_BAD_SETUP

    inbox = Inbox(
        TelegramApi(token),
        chat_id,
        InboxPaths(args.queue, args.offset_file),
        storage,
        resolve_now(args.now, pace.timezone),
    )
    try:
        handled, failed = inbox.run()
    except TelegramError as exc:
        log.error("Cannot read Telegram messages: %s", exc)
        return EXIT_FAILED
    log.info("%d Telegram message(s) handled", handled)
    return EXIT_FAILED if failed else EXIT_OK
