"""Pacing rules: on which days and hours, how far apart, and how many Stories a day."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # index == date.weekday()
DEFAULT_TIMEZONE = "America/Sao_Paulo"
# Instagram allows 100 API-published posts per rolling 24 hours; stay well under it.
MAX_DAILY_LIMIT = 90
GRACE = timedelta(seconds=90)


class PaceError(ValueError):
    """The pace file is missing fields or contains invalid values."""


@dataclass(frozen=True)
class Pace:
    timezone: ZoneInfo
    start: time
    end: time
    days: frozenset[str]  # weekdays on which Stories go out
    skip_dates: frozenset[date]  # holidays and other days with no Stories at all
    interval: timedelta
    daily_limit: int
    low_queue: int  # warn when this many Stories are left
    notify_each: bool  # message on every published Story, not only on problems
    recycle: bool  # a published art goes back to the end of the queue instead of leaving it


def _parse_time(raw: Any, field: str) -> time:
    try:
        return datetime.strptime(str(raw), "%H:%M").time()
    except ValueError:
        raise PaceError(f"'{field}' must be HH:MM (24h), got {raw!r}") from None


def _parse_int(data: Mapping[str, Any], field: str, default: int, low: int, high: int) -> int:
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PaceError(f"'{field}' must be an integer in {low}..{high}")
    return value


def parse_pace(data: Any) -> Pace:
    if not isinstance(data, dict):
        raise PaceError("pace must be a JSON object")
    try:
        tz = ZoneInfo(data.get("timezone", DEFAULT_TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        raise PaceError(f"unknown timezone {data.get('timezone')!r}") from None

    start = _parse_time(data.get("start", "09:00"), "start")
    end = _parse_time(data.get("end", "21:00"), "end")
    if end <= start:
        raise PaceError("'end' must be later than 'start'")

    days = data.get("days", list(WEEKDAYS))
    if not isinstance(days, list) or not days or not set(days) <= set(WEEKDAYS):
        raise PaceError(f"'days' must be a non-empty list of {list(WEEKDAYS)}")

    raw_skips = data.get("skip_dates", [])
    if not isinstance(raw_skips, list):
        raise PaceError("'skip_dates' must be a list of YYYY-MM-DD dates")
    try:
        skip_dates = frozenset(date.fromisoformat(str(d)) for d in raw_skips)
    except ValueError:
        raise PaceError("'skip_dates' must be a list of YYYY-MM-DD dates") from None

    return Pace(
        timezone=tz,
        start=start,
        end=end,
        days=frozenset(days),
        skip_dates=skip_dates,
        interval=timedelta(minutes=_parse_int(data, "interval_minutes", 20, 1, 24 * 60)),
        daily_limit=_parse_int(data, "daily_limit", 60, 1, MAX_DAILY_LIMIT),
        low_queue=_parse_int(data, "low_queue", 10, 0, 1000),
        notify_each=bool(data.get("notify_each", False)),
        recycle=bool(data.get("recycle", False)),
    )


def load_pace(path: Path) -> Pace:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PaceError(f"{path} is not valid JSON: {exc}") from exc
    return parse_pace(data)


def may_publish(pace: Pace, now: datetime, posted: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether one more Story may go out right now.

    The trigger (an external scheduler, or GitHub's own cron) is not punctual, so this only
    ever allows ONE Story per run, and only when `interval` has passed since the last one.
    A late trigger therefore delays Stories; it can never dump a backlog at once.

    A run that starts a few seconds early would otherwise miss the slot and push the Story a
    whole trigger period away, so GRACE of slack is allowed.
    """
    now = now.astimezone(pace.timezone)
    if WEEKDAYS[now.weekday()] not in pace.days or now.date() in pace.skip_dates:
        return False
    if not pace.start <= now.time().replace(tzinfo=None) < pace.end:
        return False

    times = [datetime.fromisoformat(entry["posted_at"]) for entry in posted.values()]
    today = sum(1 for t in times if t.astimezone(pace.timezone).date() == now.date())
    if today >= pace.daily_limit:
        return False
    wait = pace.interval - min(GRACE, pace.interval / 4)
    return not times or now - max(times) >= wait
