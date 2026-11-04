"""Command-line entry point: put every art in the failed list back into the queue."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from .cli import EXIT_BAD_SETUP, EXIT_OK, resolve_now
from .pace import PaceError, load_pace
from .story_queue import requeue_failed

log = logging.getLogger("stories_bot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stories-requeue", description="Return failed arts to the Stories queue."
    )
    parser.add_argument("--pace", type=Path, default=Path("pace.json"))
    parser.add_argument("--queue", type=Path, default=Path("state/queue.json"))
    parser.add_argument("--failed", type=Path, default=Path("state/failed.json"))
    parser.add_argument("--now", help="pretend it is this local time, e.g. 2026-10-09T18:05")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        pace = load_pace(args.pace)
    except (OSError, PaceError) as exc:
        log.error("Cannot load pace %s: %s", args.pace, exc)
        return EXIT_BAD_SETUP
    count = requeue_failed(args.queue, args.failed, resolve_now(args.now, pace.timezone))
    log.info("%d art(s) returned to the queue", count)
    return EXIT_OK
