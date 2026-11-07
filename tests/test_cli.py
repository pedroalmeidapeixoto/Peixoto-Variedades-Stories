import json
from datetime import datetime

import pytest

from stories_bot import cli
from stories_bot.instagram import InstagramClient, InstagramError
from stories_bot.notify import TelegramNotifier
from stories_bot.story_queue import MAX_ATTEMPTS, ROTATING_MAX_ATTEMPTS, add_art, load_queue

MORNING = "2026-10-09T10:00"
ENV = {
    "IG_USER_ID": "1",
    "IG_ACCESS_TOKEN": "t",
    "R2_ACCOUNT_ID": "a",
    "R2_ACCESS_KEY_ID": "k",
    "R2_SECRET_ACCESS_KEY": "s",
    "R2_BUCKET": "b",
    "R2_PUBLIC_URL": "https://cdn.example",
}


@pytest.fixture
def project(tmp_path, monkeypatch, storage):
    for name in [*ENV, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "build_storage", lambda settings: storage)
    monkeypatch.setattr(InstagramClient, "publishing_quota", lambda self: None)  # no network
    (tmp_path / "pace.json").write_text(json.dumps({"low_queue": 1}))
    return tmp_path


def configure(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def enqueue(project, storage, *names):
    return [
        add_art(
            storage,
            project / "state" / "queue.json",
            b"img",
            stem=name,
            suffix=".jpg",
            now=datetime(2026, 10, 9, 8, 0),
        )
        for name in names
    ]


def run(project, now=MORNING, *extra):
    return cli.main(
        [
            "--pace", str(project / "pace.json"),
            "--queue", str(project / "state" / "queue.json"),
            "--failed", str(project / "state" / "failed.json"),
            "--state", str(project / "state" / "posted.json"),
            "--alerts", str(project / "state" / "alerts.json"),
            "--now", now,
            *extra,
        ]
    )  # fmt: skip


def posted(project):
    return json.loads((project / "state" / "posted.json").read_text())


@pytest.fixture
def sent(monkeypatch):
    """Telegram is configured and every message is captured instead of being sent."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    messages: list[str] = []
    monkeypatch.setattr(TelegramNotifier, "send", lambda self, text: messages.append(text))
    return messages


def fake_publish(monkeypatch, result="m1", calls=None):
    def publish(self, url, *, is_video):
        if calls is not None:
            calls.append((url, is_video))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(InstagramClient, "publish_story", publish)


def test_publishes_the_head_of_the_queue_then_cleans_up(project, storage, monkeypatch):
    configure(monkeypatch)
    first, second = enqueue(project, storage, "a", "b")
    calls = []
    fake_publish(monkeypatch, calls=calls)

    assert run(project) == cli.EXIT_OK
    assert calls == [(f"https://cdn.example/{first.key}", False)]
    assert posted(project)[first.id]["media_id"] == "m1"
    assert [i.id for i in load_queue(project / "state" / "queue.json")] == [second.id]
    assert storage.deleted == [first.key]


def test_one_story_per_run_and_respects_the_interval(project, storage, monkeypatch):
    configure(monkeypatch)
    enqueue(project, storage, "a", "b", "c")
    fake_publish(monkeypatch)

    assert run(project, "2026-10-09T10:00") == cli.EXIT_OK
    assert run(project, "2026-10-09T10:05") == cli.EXIT_OK  # too soon
    assert len(posted(project)) == 1
    assert run(project, "2026-10-09T10:20") == cli.EXIT_OK
    assert len(posted(project)) == 2


def test_outside_hours_publishes_nothing_and_needs_no_credentials(project, storage):
    enqueue(project, storage, "a")
    assert run(project, "2026-10-09T23:00") == cli.EXIT_OK
    assert len(load_queue(project / "state" / "queue.json")) == 1


def test_empty_queue_succeeds_without_credentials(project):
    assert run(project) == cli.EXIT_OK


def test_dry_run_publishes_nothing(project, storage):
    enqueue(project, storage, "a")
    assert run(project, MORNING, "--dry-run") == cli.EXIT_OK
    assert not (project / "state" / "posted.json").exists()


def test_missing_credentials_is_a_setup_error_and_notifies(project, storage, sent):
    enqueue(project, storage, "a")
    assert run(project) == cli.EXIT_BAD_SETUP
    assert len(sent) == 1 and sent[0].startswith("❌ Story não publicado: configuração")


def test_invalid_pace_is_a_setup_error(project):
    (project / "pace.json").write_text("{not json")
    assert run(project) == cli.EXIT_BAD_SETUP


def test_failure_counts_an_attempt_and_keeps_the_item(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    (item,) = enqueue(project, storage, "a")
    fake_publish(monkeypatch, InstagramError("HTTP 400: nope", 100))

    assert run(project) == cli.EXIT_FAILED
    (queued,) = load_queue(project / "state" / "queue.json")
    assert queued.id == item.id and queued.attempts == 1
    assert not (project / "state" / "posted.json").exists()
    assert sent == ["❌ Falha ao publicar a.jpg: HTTP 400: nope. Tento de novo mais tarde."]


def test_item_that_keeps_failing_is_set_aside_so_the_queue_moves_on(project, storage, monkeypatch):
    configure(monkeypatch)
    bad, good = enqueue(project, storage, "bad", "good")
    fake_publish(monkeypatch, InstagramError("HTTP 400: nope", 100))
    for _ in range(MAX_ATTEMPTS):
        assert run(project) == cli.EXIT_FAILED

    assert [i.id for i in load_queue(project / "state" / "queue.json")] == [good.id]
    failed = json.loads((project / "state" / "failed.json").read_text())
    assert failed[0]["id"] == bad.id


def test_expired_token_stops_without_burning_attempts(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    enqueue(project, storage, "a")
    fake_publish(monkeypatch, InstagramError("HTTP 400: expired", 190))

    assert run(project) == cli.EXIT_BAD_SETUP
    assert load_queue(project / "state" / "queue.json")[0].attempts == 0
    assert sent == ["❌ O Instagram recusou o token (HTTP 400: expired). Confira ou gere um novo."]


def test_item_posted_but_still_queued_is_not_published_twice(project, storage, monkeypatch):
    """A run that died between recording the post and dequeuing must not repeat the Story."""
    configure(monkeypatch)
    ghost, nxt = enqueue(project, storage, "ghost", "next")
    (project / "state" / "posted.json").write_text(
        json.dumps(
            {ghost.id: {"name": "x", "media_id": "m0", "posted_at": "2026-10-08T10:00:00-03:00"}}
        )
    )
    calls = []
    fake_publish(monkeypatch, calls=calls)

    assert run(project) == cli.EXIT_OK
    assert calls == [(f"https://cdn.example/{nxt.key}", False)]


def test_notifications_follow_the_queue_level(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    enqueue(project, storage, "a", "b")
    fake_publish(monkeypatch)

    assert run(project, "2026-10-09T10:00") == cli.EXIT_OK  # 1 left == low_queue
    assert sent == ["⚠️ Fila acabando: restam 1 Stories."]
    assert run(project, "2026-10-09T10:30") == cli.EXIT_OK
    assert sent[-1].startswith("⚠️ A fila de Stories acabou")


def recycle_pace(project, **extra):
    (project / "pace.json").write_text(
        json.dumps({"recycle": True, "interval_minutes": 6, **extra})
    )


def test_recycle_sends_the_art_to_the_back_and_keeps_it_in_storage(project, storage, monkeypatch):
    configure(monkeypatch)
    recycle_pace(project)
    first, second = enqueue(project, storage, "a", "b")
    fake_publish(monkeypatch)

    assert run(project, "2026-10-09T10:00") == cli.EXIT_OK
    queue = load_queue(project / "state" / "queue.json")
    assert [i.key for i in queue] == [second.key, first.key]  # a went to the back
    assert queue[1].id == f"{first.id}@20261009100000"  # fresh id for the next lap
    assert posted(project)[first.id]["media_id"] == "m1"
    assert storage.deleted == [] and first.key in storage.objects  # the art stays in the bucket


def test_recycle_loops_through_the_whole_pack_and_starts_again(project, storage, monkeypatch):
    configure(monkeypatch)
    recycle_pace(project)
    enqueue(project, storage, "a", "b")
    calls = []
    fake_publish(monkeypatch, calls=calls)

    for minute in (0, 6, 12, 18):
        assert run(project, f"2026-10-09T10:{minute:02d}") == cli.EXIT_OK
    urls = [url.rsplit("-", 1)[-1] for url, _ in calls]
    assert urls == ["a.jpg", "b.jpg", "a.jpg", "b.jpg"]  # a, b, a, b: the same art twice
    assert len(load_queue(project / "state" / "queue.json")) == 2


def test_recycle_never_warns_about_the_queue_running_out(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    recycle_pace(project, low_queue=1)
    enqueue(project, storage, "a", "b")
    fake_publish(monkeypatch)

    assert run(project) == cli.EXIT_OK
    assert sent == []


def test_recycle_rescues_an_item_whose_run_died_after_posting(project, storage, monkeypatch):
    configure(monkeypatch)
    recycle_pace(project)
    ghost, nxt = enqueue(project, storage, "ghost", "next")
    (project / "state" / "posted.json").write_text(
        json.dumps(
            {ghost.id: {"name": "x", "media_id": "m0", "posted_at": "2026-10-08T10:00:00-03:00"}}
        )
    )
    calls = []
    fake_publish(monkeypatch, calls=calls)

    assert run(project) == cli.EXIT_OK
    assert [c[0] for c in calls] == [f"https://cdn.example/{nxt.key}"]  # no repeat of the ghost
    keys = [i.key for i in load_queue(project / "state" / "queue.json")]
    assert sorted(keys) == sorted([ghost.key, nxt.key])  # the ghost was kept, not dropped


def test_recycle_forgets_old_post_records(project, storage, monkeypatch):
    configure(monkeypatch)
    recycle_pace(project)
    enqueue(project, storage, "a")
    (project / "state" / "posted.json").write_text(
        json.dumps(
            {"old": {"name": "x", "media_id": "m0", "posted_at": "2026-09-01T10:00:00-03:00"}}
        )
    )
    fake_publish(monkeypatch)

    assert run(project) == cli.EXIT_OK
    assert "old" not in posted(project) and len(posted(project)) == 1


def test_notify_each_publish_when_enabled(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    (project / "pace.json").write_text(json.dumps({"notify_each": True, "low_queue": 0}))
    enqueue(project, storage, "a", "b")
    fake_publish(monkeypatch)

    assert run(project) == cli.EXIT_OK
    assert sent == ["✅ Story publicado: a.jpg (09/10 10:00). Fila: 1"]


RATE_LIMIT = InstagramError("HTTP 400: User is performing too many actions", 4)


def test_rate_limit_waits_without_burning_attempts_or_dropping_the_art(
    project, storage, monkeypatch, sent
):
    configure(monkeypatch)
    (item,) = enqueue(project, storage, "a")
    fake_publish(monkeypatch, RATE_LIMIT)

    for minute in ("10:00", "10:06", "10:12"):  # a stretch of throttled runs
        assert run(project, f"2026-10-09T{minute}") == cli.EXIT_OK
    (queued,) = load_queue(project / "state" / "queue.json")
    assert queued.id == item.id and queued.attempts == 0
    assert not (project / "state" / "failed.json").exists()
    assert len(sent) == 1 and sent[0].startswith("⏸️ O Instagram limitou")  # told once, not 3x


def test_a_recycled_queue_rotates_a_failing_art_instead_of_dropping_it(
    project, storage, monkeypatch
):
    configure(monkeypatch)
    recycle_pace(project)
    enqueue(project, storage, "bad", "good")
    calls = []

    def publish(self, url, *, is_video):
        calls.append(url)
        if "bad" in url:
            raise InstagramError("HTTP 400: The requested resource does not exist", 100)
        return "m1"

    monkeypatch.setattr(InstagramClient, "publish_story", publish)

    assert run(project, "2026-10-09T10:00") == cli.EXIT_FAILED
    queue = load_queue(project / "state" / "queue.json")
    assert [i.name for i in queue] == ["good.jpg", "bad.jpg"]  # the bad one went to the back
    assert queue[1].attempts == 1
    assert queue[1].last_error == "HTTP 400: The requested resource does not exist"
    assert run(project, "2026-10-09T10:06") == cli.EXIT_OK  # and the good one is not held up
    assert any("good" in c for c in calls)


def test_a_recycled_art_is_only_set_aside_after_many_laps(project, storage, monkeypatch):
    configure(monkeypatch)
    recycle_pace(project)
    enqueue(project, storage, "bad")
    fake_publish(monkeypatch, InstagramError("HTTP 400: nope", 100))

    for n in range(ROTATING_MAX_ATTEMPTS - 1):
        run(project, f"2026-10-09T10:{n * 6:02d}")
    assert len(load_queue(project / "state" / "queue.json")) == 1  # still in the pack
    run(project, "2026-10-09T11:00")
    assert load_queue(project / "state" / "queue.json") == []
    assert json.loads((project / "state" / "failed.json").read_text())[0]["name"] == "bad.jpg"


def test_failure_messages_are_not_repeated_every_run(project, storage, monkeypatch, sent):
    configure(monkeypatch)
    enqueue(project, storage, "a", "b", "c")
    fake_publish(monkeypatch, InstagramError("HTTP 400: nope", 100))

    run(project, "2026-10-09T10:00")
    run(project, "2026-10-09T10:06")
    assert len(sent) == 1


def test_a_full_meta_quota_waits_without_trying(project, storage, monkeypatch):
    configure(monkeypatch)
    enqueue(project, storage, "a")
    monkeypatch.setattr(InstagramClient, "publishing_quota", lambda self: (50, 50))
    calls = []
    fake_publish(monkeypatch, calls=calls)

    assert run(project) == cli.EXIT_OK
    assert calls == [] and not (project / "state" / "posted.json").exists()


def test_a_quota_with_room_publishes_normally(project, storage, monkeypatch):
    configure(monkeypatch)
    enqueue(project, storage, "a")
    monkeypatch.setattr(InstagramClient, "publishing_quota", lambda self: (10, 50))
    calls = []
    fake_publish(monkeypatch, calls=calls)

    assert run(project) == cli.EXIT_OK and len(calls) == 1
