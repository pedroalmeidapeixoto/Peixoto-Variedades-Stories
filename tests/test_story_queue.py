import json
from datetime import datetime

import pytest

from stories_bot.story_queue import (
    MAX_ATTEMPTS,
    add_art,
    load_queue,
    record_attempt,
    remove_item,
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
