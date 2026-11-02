import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from stories_bot.pace import PaceError, load_pace, may_publish, parse_pace

TZ = ZoneInfo("America/Sao_Paulo")


def at(hour: int, minute: int = 0, day: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def posted_at(*moments: datetime) -> dict[str, dict[str, str]]:
    return {f"id{i}": {"posted_at": m.isoformat()} for i, m in enumerate(moments)}


@pytest.fixture
def pace():
    return parse_pace({"start": "09:00", "end": "21:00", "interval_minutes": 20, "daily_limit": 3})


def test_defaults():
    pace = parse_pace({})
    assert (pace.start.hour, pace.end.hour) == (9, 21)
    assert pace.interval.total_seconds() == 20 * 60
    assert pace.notify_each is False


def test_first_story_of_the_day_goes_out_at_opening(pace):
    assert may_publish(pace, at(9, 0), {})
    assert may_publish(pace, at(9, 0), posted_at(at(20, 50, day=8)))


def test_days_default_to_every_day_and_can_skip_sunday():
    every_day = parse_pace({})
    mon_to_sat = parse_pace({"days": ["mon", "tue", "wed", "thu", "fri", "sat"]})
    sunday, saturday = at(10, 0, day=11), at(10, 0, day=10)  # 11 Oct 2026 is a Sunday

    assert may_publish(every_day, sunday, {})
    assert may_publish(mon_to_sat, saturday, {})
    assert not may_publish(mon_to_sat, sunday, {})


def test_weekday_is_judged_in_local_time():
    pace = parse_pace({"days": ["sat"], "end": "23:59"})
    # 22:00 local on Saturday is already Sunday in UTC; it still counts as Saturday.
    saturday_night = at(22, 0, day=10).astimezone(ZoneInfo("UTC"))
    assert saturday_night.weekday() == 6
    assert may_publish(pace, saturday_night, {})


def test_nothing_outside_the_window(pace):
    assert not may_publish(pace, at(8, 59), {})
    assert not may_publish(pace, at(21, 0), {})
    assert may_publish(pace, at(20, 59), {})


def test_respects_the_interval_since_the_last_story(pace):
    last = posted_at(at(10, 0))
    assert not may_publish(pace, at(10, 18), last)
    assert may_publish(pace, at(10, 20), last)


def test_a_run_a_few_seconds_early_still_takes_the_slot(pace):
    last = posted_at(at(10, 0))
    early = datetime(2026, 10, 9, 10, 18, 40, tzinfo=TZ)  # 18m40s after the last Story
    assert may_publish(pace, early, last)  # within the 90 s grace of the 20 min interval


def test_grace_is_capped_for_short_intervals():
    pace = parse_pace({"interval_minutes": 2})
    last = posted_at(at(10, 0))
    assert not may_publish(pace, at(10, 1), last)  # grace is at most a quarter of the interval
    assert may_publish(pace, datetime(2026, 10, 9, 10, 1, 31, tzinfo=TZ), last)


def test_late_trigger_allows_only_one_story(pace):
    """After a long silence the pace allows one Story; the next needs a fresh interval."""
    assert may_publish(pace, at(15, 0), posted_at(at(10, 0)))
    assert not may_publish(pace, at(15, 5), posted_at(at(10, 0), at(15, 0)))


def test_daily_limit_counts_local_calendar_day(pace):
    three = posted_at(at(9, 0), at(10, 0), at(11, 0))
    assert not may_publish(pace, at(12, 0), three)
    assert may_publish(pace, at(12, 0, day=10), three)  # next day: limit resets


def test_daily_limit_uses_local_time_not_utc(pace):
    # 22:30 local on the 8th is already the 9th in UTC; it must not count toward the 9th.
    late = posted_at(at(22, 30, day=8), at(22, 40, day=8), at(22, 50, day=8))
    assert may_publish(pace, at(9, 0), late)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"timezone": "Mars/Base"},
        {"start": "9am"},
        {"start": "21:00", "end": "09:00"},
        {"days": []},
        {"days": ["monday"]},
        {"days": "mon"},
        {"interval_minutes": 0},
        {"interval_minutes": True},
        {"daily_limit": 500},
        {"low_queue": -1},
    ],
)
def test_invalid_pace_is_rejected(data):
    with pytest.raises(PaceError):
        parse_pace(data)


def test_load_pace_rejects_bad_json(tmp_path):
    path = tmp_path / "pace.json"
    path.write_text("{nope")
    with pytest.raises(PaceError):
        load_pace(path)
    path.write_text(json.dumps({"interval_minutes": 5}))
    assert load_pace(path).interval.total_seconds() == 300
