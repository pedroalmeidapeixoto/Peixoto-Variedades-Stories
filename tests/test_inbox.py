import json
from datetime import datetime
from pathlib import Path

import pytest

from stories_bot import inbox_cli
from stories_bot.inbox import (
    Attachment,
    Inbox,
    InboxPaths,
    UnsupportedMedia,
    extract_attachment,
    wants_front,
)
from stories_bot.story_queue import load_queue
from stories_bot.telegram import TelegramError

CHAT = "42"
NOW = datetime(2026, 10, 9, 12, 0)


def png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\0\0\0\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


class FakeApi:
    def __init__(self, updates=(), files=None):
        self.updates = list(updates)
        self.files = files or {}
        self.sent: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "getUpdates":
            return self.updates
        if method == "getFile":
            return {"file_path": params["file_id"]}
        if method == "sendMessage":
            self.sent.append(params["text"])
            return {}
        raise AssertionError(method)

    def download(self, file_path):
        data = self.files[file_path]
        if isinstance(data, Exception):
            raise data
        return data


def doc(update_id, name, file_id=None, caption=None, chat=CHAT, chat_type="private"):
    message = {
        "message_id": update_id,
        "chat": {"id": int(chat), "type": chat_type},
        "document": {"file_id": file_id or name, "file_name": name},
    }
    if caption:
        message["caption"] = caption
    return {"update_id": update_id, "message": message}


def text(update_id, body, chat=CHAT):
    message = {"message_id": update_id, "chat": {"id": int(chat), "type": "private"}, "text": body}
    return {"update_id": update_id, "message": message}


def make(tmp_path, storage, api):
    paths = InboxPaths(tmp_path / "queue.json", tmp_path / "telegram.json")
    return Inbox(api, CHAT, paths, storage, NOW), paths


def test_arts_are_queued_in_order_with_one_summary(tmp_path, storage):
    api = FakeApi(
        [doc(1, "camisa.png"), doc(2, "tenis.png")],
        {"camisa.png": png_header(1080, 1920), "tenis.png": png_header(1080, 1920)},
    )
    inbox, paths = make(tmp_path, storage, api)

    assert inbox.run() == (2, False)
    assert [i.name for i in load_queue(paths.queue)] == ["camisa.png", "tenis.png"]
    assert len(storage.objects) == 2
    assert api.sent == ["✅ Recebi 2 arte(s). Na fila agora: 2."]
    assert json.loads(paths.offset.read_text())["offset"] == 3


def test_primeiro_in_caption_jumps_the_queue(tmp_path, storage):
    api = FakeApi(
        [doc(1, "a.png"), doc(2, "b.png", caption="Primeiro")],
        {"a.png": png_header(1080, 1920), "b.png": png_header(1080, 1920)},
    )
    inbox, paths = make(tmp_path, storage, api)
    inbox.run()
    assert [i.name for i in load_queue(paths.queue)] == ["b.png", "a.png"]


def test_wrong_ratio_and_compressed_photos_are_warned_about(tmp_path, storage):
    photo = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 42, "type": "private"},
            "photo": [{"file_id": "small", "width": 90, "height": 90}],
        },
    }
    api = FakeApi(
        [photo, doc(2, "wide.png")], {"small": b"jpeg", "wide.png": png_header(1600, 900)}
    )
    inbox, _ = make(tmp_path, storage, api)
    inbox.run()
    (summary,) = api.sent
    assert "enviada como foto" in summary and "1600×900" in summary


def test_strangers_and_groups_are_ignored_silently(tmp_path, storage):
    api = FakeApi(
        [
            doc(1, "a.png", chat="99"),
            doc(2, "b.png", chat_type="group"),
            text(3, "/fila", chat="99"),
        ],
        {"a.png": b"x", "b.png": b"x"},
    )
    inbox, paths = make(tmp_path, storage, api)
    assert inbox.run() == (3, False)
    assert api.sent == [] and storage.objects == {}
    assert not paths.queue.exists()


def test_fila_lists_the_queue(tmp_path, storage):
    api = FakeApi([text(1, "/fila")])
    inbox, paths = make(tmp_path, storage, api)
    inbox.run()
    assert api.sent == ["A fila está vazia."]

    api = FakeApi(
        [doc(1, f"p{n}.png") for n in range(12)] + [text(20, "/fila")],
        {f"p{n}.png": png_header(1080, 1920) for n in range(12)},
    )
    inbox, paths = make(tmp_path, storage, api)
    inbox.run()
    listing = api.sent[-1]
    assert listing.startswith("Na fila: 12. Próximos:\n1. p0.png")
    assert "10. p9.png" in listing and listing.endswith("… e mais 2.")


def test_remover_deletes_from_queue_and_storage(tmp_path, storage):
    api = FakeApi(
        [doc(1, "a.png"), doc(2, "b.png")],
        {"a.png": png_header(1080, 1920), "b.png": png_header(1080, 1920)},
    )
    inbox, paths = make(tmp_path, storage, api)
    inbox.run()
    gone = load_queue(paths.queue)[0]

    api2 = FakeApi([text(3, "/remover 1"), text(4, "/remover 9"), text(5, "/remover")])
    inbox2 = Inbox(api2, CHAT, paths, storage, NOW)
    inbox2.run()
    assert api2.sent[0] == "🗑️ Removi a.png da fila."
    assert api2.sent[1] == api2.sent[2] == "Use /remover N, com N sendo o número do item em /fila."
    assert gone.key in storage.deleted
    assert [i.name for i in load_queue(paths.queue)] == ["b.png"]


def test_unsupported_file_and_help(tmp_path, storage):
    api = FakeApi([doc(1, "doc.pdf"), text(2, "/start"), text(3, "oi")])
    inbox, _ = make(tmp_path, storage, api)
    inbox.run()
    assert api.sent[0].startswith("Formato não suportado")
    assert "Peixoto Variedades" in api.sent[1]
    assert api.sent[2].startswith("Como usar:")


def test_too_big_file_gets_a_clear_reply(tmp_path, storage):
    api = FakeApi([doc(1, "huge.mp4")], {"huge.mp4": TelegramError("HTTP 400: file is too big")})
    inbox, paths = make(tmp_path, storage, api)
    assert inbox.run() == (1, False)
    assert "20 MB" in api.sent[0] and not paths.queue.exists()


def test_a_failure_stops_the_run_and_keeps_the_rest_for_next_time(tmp_path, storage):
    api = FakeApi(
        [doc(1, "ok.png"), doc(2, "boom.png"), doc(3, "later.png")],
        {"ok.png": png_header(1080, 1920), "boom.png": TelegramError("HTTP 500: x")},
    )
    inbox, paths = make(tmp_path, storage, api)
    assert inbox.run() == (1, True)
    assert json.loads(paths.offset.read_text())["offset"] == 2  # resumes at the failed message
    assert api.sent == ["✅ Recebi 1 arte(s). Na fila agora: 1."]


def test_extract_attachment_and_front_helpers():
    assert extract_attachment({"text": "hi"}) is None
    assert extract_attachment(
        {"document": {"file_id": "f", "file_name": "x", "mime_type": "video/mp4"}}
    ) == Attachment("f", "x", ".mp4")
    with pytest.raises(UnsupportedMedia):
        extract_attachment({"document": {"file_id": "f", "file_name": "x.zip"}})
    assert wants_front("É URGENTE!") is False
    assert wants_front("urgente") and not wants_front(None)


def test_inbox_cli_without_telegram_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert inbox_cli.main(["--pace", str(Path(tmp_path) / "none.json")]) == inbox_cli.EXIT_OK


def test_inbox_cli_with_bad_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    assert inbox_cli.main(["--pace", str(tmp_path / "missing.json")]) == inbox_cli.EXIT_BAD_SETUP
