"""Command-line entry point: upload arts from your computer and add them to the queue.

Meant for bulk loads (a folder of ready arts). Needs the R2 variables in the environment
(see `.env.example`); commit `state/queue.json` afterwards.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from .cli import EXIT_BAD_SETUP, EXIT_FAILED, EXIT_OK, build_storage, resolve_now
from .config import ConfigError, R2Settings
from .media import image_size, is_story_ratio
from .pace import DEFAULT_TIMEZONE
from .storage import CONTENT_TYPES, StorageError
from .story_queue import add_art

log = logging.getLogger("stories_bot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stories-enqueue", description="Upload arts and add them to the Stories queue."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="art files or folders of arts")
    parser.add_argument("--queue", type=Path, default=Path("state/queue.json"))
    parser.add_argument("--front", action="store_true", help="put them ahead of the queue")
    return parser


def collect_files(paths: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        found = sorted(path.iterdir()) if path.is_dir() else [path]
        files.extend(f for f in found if f.suffix.lower() in CONTENT_TYPES)
    return files


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        storage = build_storage(R2Settings.from_env())
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EXIT_BAD_SETUP

    tz = ZoneInfo(DEFAULT_TIMEZONE)
    files = collect_files(args.paths)
    if not files:
        log.error("No JPG, PNG, MP4 or MOV files found")
        return EXIT_BAD_SETUP

    # `--front` keeps the order given: add them in reverse so the first file ends up first.
    ordered = list(reversed(files)) if args.front else files
    failures = 0
    for path in ordered:
        data = path.read_bytes()
        size = image_size(data) if path.suffix.lower() in {".jpg", ".jpeg", ".png"} else None
        if size and not is_story_ratio(*size):
            log.warning("%s is %dx%d, not 9:16; Instagram may crop or reject it", path.name, *size)
        try:
            item = add_art(
                storage,
                args.queue,
                data,
                stem=path.stem,
                suffix=path.suffix,
                now=resolve_now(None, tz),
                front=args.front,
            )
        except StorageError as exc:
            log.error("%s: %s", path.name, exc)
            failures += 1
            continue
        log.info("Queued %s as %s", path.name, item.id)

    return EXIT_FAILED if failures else EXIT_OK
