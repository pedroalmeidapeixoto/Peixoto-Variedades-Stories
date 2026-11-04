import json

from stories_bot import requeue_cli
from stories_bot.story_queue import load_queue


def test_requeue_cli_restores_failed_arts(tmp_path):
    (tmp_path / "pace.json").write_text("{}")
    entry = {
        "id": "20261102-aaa",
        "key": "stories/20261102-aaa-01-a.jpg",
        "name": "01-a.jpg",
        "added_at": "2026-11-02T10:00:00-03:00",
        "attempts": 3,
        "error": "HTTP 400: User is performing too many actions",
    }
    (tmp_path / "failed.json").write_text(json.dumps([entry]))
    args = ["--pace", str(tmp_path / "pace.json"), "--queue", str(tmp_path / "queue.json")]
    args += ["--failed", str(tmp_path / "failed.json"), "--now", "2026-11-04T10:00"]

    assert requeue_cli.main(args) == 0
    (item,) = load_queue(tmp_path / "queue.json")
    assert item.name == "01-a.jpg" and item.attempts == 0 and item.id.startswith("20261102-aaa@")
    assert json.loads((tmp_path / "failed.json").read_text()) == []


def test_requeue_cli_needs_a_valid_pace(tmp_path):
    assert requeue_cli.main(["--pace", str(tmp_path / "missing.json")]) == 2
