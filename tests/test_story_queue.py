import json
from datetime import datetime

import pytest

from stories_bot.story_queue import (
    MAX_ATTEMPTS,
    add_art,
    load_queue,
    record_attempt,
    recycle_item,
    remove_item,
    requeue_failed,
)

NOW = datetime(2026, 10, 9, 12, 0)


def add(storage, path, stem="Camisa Polo", suffix=".JPG", **kwargs):
    return add_art(storage, path, b"data", stem=stem, suffix=suffix, now=NOW, **kwargs)


def test_add_art_uploads_then_queues(tmp_path, storage):
    queue = tmp_path / "queue.json"
    item = add(storage, queue)
    assert storage.objects[item.key] == b"data"
    assert item.key.startswith("stories/") and item.key.endswith("-camisa-polo.jpg")
    assert item.name == "Camisa Polo.jpg"
    assert [i.id for i in load_queue(queue)] == [item.id]


def test_queue_is_fifo_and_front_jumps_the_line(tmp_path, storage):
    queue = tmp_path / "queue.json"
    first, second = add(storage, queue, "a"), add(storage, queue, "b")
    urgent = add(storage, queue, "c", front=True)
    assert [i.id for i in load_queue(queue)] == [urgent.id, first.id, second.id]


def test_unsupported_type_is_rejected_before_uploading(tmp_path, storage):
    with pytest.raises(ValueError):
        add(storage, tmp_path / "queue.json", suffix=".gif")
    assert storage.objects == {}


def test_failed_upload_leaves_the_queue_untouched(tmp_path, storage, monkeypatch):
    def boom(*args):
        raise RuntimeError("down")

    monkeypatch.setattr(storage, "upload", boom)
    queue = tmp_path / "queue.json"
    with pytest.raises(RuntimeError):
        add(storage, queue)
    assert not queue.exists()


def test_video_detection(tmp_path, storage):
    assert add(storage, tmp_path / "q.json", suffix=".mp4").is_video
    assert not add(storage, tmp_path / "q.json", suffix=".png").is_video


def test_remove_item(tmp_path, storage):
    queue = tmp_path / "queue.json"
    a, b = add(storage, queue, "a"), add(storage, queue, "b")
    assert remove_item(queue, a.id) == a
    assert [i.id for i in load_queue(queue)] == [b.id]
    assert remove_item(queue, "ghost") is None


def test_recycle_item_moves_it_to_the_back_with_a_new_id_and_clean_attempts(tmp_path, storage):
    queue, failed = tmp_path / "queue.json", tmp_path / "failed.json"
    a, b = add(storage, queue, "a"), add(storage, queue, "b")
    record_attempt(queue, failed, a.id, "HTTP 400")  # a has one failed attempt

    assert recycle_item(queue, a.id, "lap2") is True
    items = load_queue(queue)
    assert [(i.id, i.key, i.attempts) for i in items] == [(b.id, b.key, 0), ("lap2", a.key, 0)]
    assert recycle_item(queue, "ghost", "x") is False


def test_item_is_set_aside_after_repeated_failures(tmp_path, storage):
    queue, failed = tmp_path / "queue.json", tmp_path / "failed.json"
    bad, good = add(storage, queue, "bad"), add(storage, queue, "good")

    for _ in range(MAX_ATTEMPTS - 1):
        assert record_attempt(queue, failed, bad.id, "HTTP 400") is False
    assert load_queue(queue)[0].attempts == MAX_ATTEMPTS - 1

    assert record_attempt(queue, failed, bad.id, "HTTP 400") is True
    assert [i.id for i in load_queue(queue)] == [good.id]
    (entry,) = json.loads(failed.read_text())
    assert entry["id"] == bad.id and entry["error"] == "HTTP 400"


def test_record_attempt_on_unknown_item_is_a_noop(tmp_path):
    assert record_attempt(tmp_path / "q.json", tmp_path / "f.json", "ghost", "x") is False


def test_rotating_a_failure_sends_it_to_the_back_and_keeps_counting(tmp_path, storage):
    queue, failed = tmp_path / "queue.json", tmp_path / "failed.json"
    a, b, c = (add(storage, queue, n) for n in "abc")
    assert record_attempt(queue, failed, a.id, "x", rotate=True, max_attempts=3) is False
    assert [i.id for i in load_queue(queue)] == [b.id, c.id, a.id]
    assert load_queue(queue)[-1].attempts == 1
    assert load_queue(queue)[-1].last_error == "x"
    # a successful lap clears the note
    assert recycle_item(queue, a.id, "lap2") and load_queue(queue)[-1].last_error == ""


def test_requeue_failed_restores_everything_in_posting_order(tmp_path, storage):
    queue, failed = tmp_path / "queue.json", tmp_path / "failed.json"
    items = {n: add(storage, queue, n) for n in ("03-c", "01-a", "02-b", "04-d")}
    # 01 and 02 failed and were dropped; the queue now holds 03 (next) and 04
    for n in ("01-a", "02-b"):
        record_attempt(queue, failed, items[n].id, "too many actions", max_attempts=1)
    assert [i.name for i in load_queue(queue)] == ["03-c.jpg", "04-d.jpg"]

    assert requeue_failed(queue, failed, NOW) == 2
    # the whole pack again, with the art that was next (03) still first, then 04, 01, 02
    assert [i.name for i in load_queue(queue)] == ["03-c.jpg", "04-d.jpg", "01-a.jpg", "02-b.jpg"]
    assert json.loads(failed.read_text()) == []
    assert all(i.attempts == 0 for i in load_queue(queue))
    assert requeue_failed(queue, failed, NOW) == 0  # nothing left to restore


def test_requeue_failed_never_duplicates_an_art_already_queued(tmp_path, storage):
    queue, failed = tmp_path / "queue.json", tmp_path / "failed.json"
    a = add(storage, queue, "a")
    failed.write_text(json.dumps([{**a.__dict__, "error": "x"}]))
    assert requeue_failed(queue, failed, NOW) == 0
    assert len(load_queue(queue)) == 1
