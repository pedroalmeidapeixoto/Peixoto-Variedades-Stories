import pytest

from stories_bot import cli, enqueue_cli
from stories_bot.story_queue import load_queue

R2_ENV = {
    "R2_ACCOUNT_ID": "a",
    "R2_ACCESS_KEY_ID": "k",
    "R2_SECRET_ACCESS_KEY": "s",
    "R2_BUCKET": "b",
    "R2_PUBLIC_URL": "https://cdn.example",
}


@pytest.fixture
def configured(monkeypatch, storage):
    for name, value in R2_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(enqueue_cli, "build_storage", lambda settings: storage)


def test_folder_is_uploaded_in_name_order_ignoring_other_files(tmp_path, storage, configured):
    arts = tmp_path / "arts"
    arts.mkdir()
    for name in ("b.jpg", "a.png", "notes.txt"):
        (arts / name).write_bytes(b"x")
    queue = tmp_path / "queue.json"

    assert enqueue_cli.main([str(arts), "--queue", str(queue)]) == cli.EXIT_OK
    assert [i.name for i in load_queue(queue)] == ["a.png", "b.jpg"]
    assert len(storage.objects) == 2


def test_front_keeps_the_given_order_ahead_of_the_queue(tmp_path, storage, configured):
    queue = tmp_path / "queue.json"
    old = tmp_path / "old.jpg"
    old.write_bytes(b"x")
    enqueue_cli.main([str(old), "--queue", str(queue)])
    new = [tmp_path / "n1.jpg", tmp_path / "n2.jpg"]
    for path in new:
        path.write_bytes(b"x")

    enqueue_cli.main([*map(str, new), "--front", "--queue", str(queue)])
    assert [i.name for i in load_queue(queue)] == ["n1.jpg", "n2.jpg", "old.jpg"]


def test_nothing_to_upload_is_a_setup_error(tmp_path, configured):
    (tmp_path / "x.txt").write_text("hi")
    assert enqueue_cli.main([str(tmp_path / "x.txt")]) == cli.EXIT_BAD_SETUP


def test_missing_r2_settings_is_a_setup_error(tmp_path, monkeypatch):
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    assert enqueue_cli.main([str(tmp_path)]) == cli.EXIT_BAD_SETUP
