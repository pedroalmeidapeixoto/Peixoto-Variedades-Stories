"""Receive arts through a private Telegram chat and put them in the queue.

The bot is polled (`getUpdates`) from the scheduled workflow, so there is no server. Only the
configured chat is obeyed: anyone else who finds the bot is ignored without a reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .media import image_size, is_story_ratio
from .state import load_offset, save_offset
from .storage import Storage, StorageError
from .story_queue import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    add_art,
    load_queue,
    remove_item,
)
from .telegram import TelegramApi, TelegramError

log = logging.getLogger("stories_bot")

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}

HELP = (
    "Como usar:\n"
    "• Envie as artes dos Stories como ARQUIVO (clipe de papel → Arquivo), em JPG, PNG, MP4 ou "
    "MOV, no tamanho 1080×1920. Pode mandar várias de uma vez.\n"
    "• Elas entram no fim da fila e saem uma a uma, no ritmo definido em pace.json.\n"
    '• Escreva "primeiro" na legenda para a arte furar a fila.\n'
    "• /fila mostra o que está na fila.\n"
    "• /remover N tira da fila o item de número N."
)

QUEUE_PREVIEW = 10


class UnsupportedMedia(ValueError):
    """The attachment is not a JPG, PNG, MP4 or MOV."""


@dataclass(frozen=True)
class Attachment:
    file_id: str
    stem: str
    suffix: str
    compressed_photo: bool = False


@dataclass(frozen=True)
class InboxPaths:
    queue: Path
    offset: Path


def extract_attachment(message: dict[str, Any]) -> Attachment | None:
    """The art in a message, None if it carries no media, UnsupportedMedia if it is not usable."""
    if message.get("photo"):
        largest = max(message["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))
        return Attachment(largest["file_id"], "foto", ".jpg", compressed_photo=True)

    media = message.get("document") or message.get("video")
    if media is None:
        return None
    name = media.get("file_name") or ""
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        suffix = MIME_EXTENSIONS.get(media.get("mime_type", ""), "")
    if not suffix:
        raise UnsupportedMedia(name or "unknown")
    return Attachment(media["file_id"], Path(name).stem or "arte", suffix)


def wants_front(caption: str | None) -> bool:
    words = (caption or "").lower().split()
    return "primeiro" in words or "urgente" in words


class Inbox:
    def __init__(
        self,
        api: TelegramApi,
        chat_id: str,
        paths: InboxPaths,
        storage: Storage,
        now: datetime,
    ) -> None:
        self._api = api
        self._chat_id = chat_id
        self._paths = paths
        self._storage = storage
        self._now = now
        self._received: list[str] = []
        self._warnings: list[str] = []

    def run(self) -> tuple[int, bool]:
        """Handle pending messages. Returns (messages handled, whether it stopped on a failure).

        The offset is saved after every message, so a failure part-way leaves the rest
        waiting for the next run (Telegram keeps them for 24 hours).
        """
        params: dict[str, Any] = {"timeout": 0, "limit": 100, "allowed_updates": ["message"]}
        offset = load_offset(self._paths.offset)
        if offset is not None:
            params["offset"] = offset

        handled = 0
        failed = False
        for update in self._api.call("getUpdates", params):
            try:
                if update.get("message"):
                    self._handle(update["message"])
            except (TelegramError, StorageError) as exc:
                log.error("Stopped at update %s: %s", update["update_id"], exc)
                failed = True
                break
            save_offset(self._paths.offset, update["update_id"] + 1)
            handled += 1
        self._flush()
        return handled, failed

    # -- message handling ---------------------------------------------------------------

    def _handle(self, message: dict[str, Any]) -> None:
        chat = message["chat"]
        if chat.get("type") != "private" or str(chat["id"]) != self._chat_id:
            log.warning("Ignoring a message from an unauthorized chat")
            return
        try:
            attachment = extract_attachment(message)
        except UnsupportedMedia:
            self._reply("Formato não suportado. Envie JPG, PNG, MP4 ou MOV.")
            return
        if attachment is None:
            self._handle_text(message.get("text") or "")
        else:
            self._handle_art(attachment, message.get("caption"))

    def _handle_text(self, text: str) -> None:
        self._flush()  # keep replies in the order the messages were sent
        parts = text.split()
        command = parts[0].split("@")[0].lower() if parts and text.startswith("/") else ""
        if command == "/start":
            self._reply("Olá! Eu publico Stories no Instagram da Peixoto Variedades.\n\n" + HELP)
        elif command == "/fila":
            self._reply(self._queue_text())
        elif command == "/remover":
            self._reply(self._remove(parts[1] if len(parts) > 1 else ""))
        else:
            self._reply(HELP)

    def _handle_art(self, art: Attachment, caption: str | None) -> None:
        try:
            file_info = self._api.call("getFile", {"file_id": art.file_id})
            data = self._api.download(file_info["file_path"])
        except TelegramError as exc:
            if "too big" in str(exc).lower():
                self._reply("Arquivo grande demais: o Telegram só entrega arquivos de até 20 MB.")
                return
            raise

        item = add_art(
            self._storage,
            self._paths.queue,
            data,
            stem=art.stem,
            suffix=art.suffix,
            now=self._now,
            front=wants_front(caption),
        )
        self._received.append(item.name)
        if art.compressed_photo:
            self._warnings.append(
                f"⚠️ {item.name}: foi enviada como foto e o Telegram comprime. "
                "Prefira enviar como arquivo."
            )
        size = image_size(data) if art.suffix in IMAGE_EXTENSIONS else None
        if size and not is_story_ratio(*size):
            self._warnings.append(
                f"⚠️ {item.name}: {size[0]}×{size[1]}, fora de 9:16 (1080×1920). "
                "O Instagram pode cortar ou recusar."
            )

    def _flush(self) -> None:
        """One summary per run, so a batch of 30 arts is one message and not thirty."""
        if not self._received:
            return
        total = len(load_queue(self._paths.queue))
        lines = [f"✅ Recebi {len(self._received)} arte(s). Na fila agora: {total}."]
        lines += self._warnings
        self._reply("\n".join(lines))
        self._received, self._warnings = [], []

    def _queue_text(self) -> str:
        items = load_queue(self._paths.queue)
        if not items:
            return "A fila está vazia."
        lines = [f"Na fila: {len(items)}. Próximos:"]
        lines += [f"{n}. {item.name}" for n, item in enumerate(items[:QUEUE_PREVIEW], start=1)]
        if len(items) > QUEUE_PREVIEW:
            lines.append(f"… e mais {len(items) - QUEUE_PREVIEW}.")
        return "\n".join(lines)

    def _remove(self, argument: str) -> str:
        items = load_queue(self._paths.queue)
        if not argument.isdigit() or not 1 <= int(argument) <= len(items):
            return "Use /remover N, com N sendo o número do item em /fila."
        item = items[int(argument) - 1]
        remove_item(self._paths.queue, item.id)
        try:
            self._storage.delete(item.key)
        except StorageError as exc:
            log.warning("Could not delete %s from storage: %s", item.key, exc)
        return f"🗑️ Removi {item.name} da fila."

    def _reply(self, text: str) -> None:
        try:
            self._api.call("sendMessage", {"chat_id": self._chat_id, "text": text})
        except TelegramError as exc:
            log.warning("Could not reply on Telegram: %s", exc)
