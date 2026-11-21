"""Doubles for Telegram: an HTTP client that answers like the Bot API, and the updates it gets.

Nothing here touches the network: `FakeRequest` replaces the HTTP client of `python-telegram-bot`,
so the real library code builds the requests and parses the answers.
"""

import json
from typing import Any

from telegram import Update
from telegram.request import BaseRequest, RequestData

CHAT_ID = 987654321
OTHER_CHAT_ID = 555000111
TOKEN = "test-token-telegram-456"
MAX_CALLS = 50
"""Tope del doble: un código que lo llama en bucle falla con un error claro."""

BOT_USER = {"id": 111, "is_bot": True, "first_name": "Prueba", "username": "prueba_bot"}


def sent_message(chat_id: int = CHAT_ID, message_id: int = 10, text: str = "x") -> dict[str, Any]:
    return {
        "message_id": message_id,
        "date": 1_700_000_000,
        "chat": {"id": chat_id, "type": "private", "first_name": "P"},
        "text": text,
    }


class FakeRequest(BaseRequest):
    """Records every call to the Bot API and answers with what `failures` or `results` say."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, tuple[int, str]] = {}
        """Method name -> (HTTP status, description): the call fails like Telegram would."""

    @property
    def read_timeout(self) -> float | None:
        return 1.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def methods(self) -> list[str]:
        """The Bot API methods called so far, without the initial `getMe`."""
        return [name for name, _ in self.calls if name != "getMe"]

    def params(self, method: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == method]

    async def do_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, url: str, method: str, request_data: RequestData | None = None, **timeouts: Any
    ) -> tuple[int, bytes]:
        name = url.rsplit("/", 1)[1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((name, params))
        if len(self.calls) > MAX_CALLS:
            raise AssertionError("se ha llamado a la API de Telegram en bucle")
        if name in self.failures:
            status, description = self.failures[name]
            body = {"ok": False, "error_code": status, "description": description}
            return status, json.dumps(body).encode()
        result: Any = True
        if name == "getMe":
            result = BOT_USER
        elif name in ("sendMessage", "editMessageText"):
            result = sent_message(text=str(params.get("text", "")))
        return 200, json.dumps({"ok": True, "result": result}).encode()


def callback_update(
    data: str, chat_id: int = CHAT_ID, message_id: int = 10, update_id: int = 1
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq{update_id}",
            "from": {"id": chat_id, "is_bot": False, "first_name": "P"},
            "chat_instance": "ci",
            "data": data,
            "message": sent_message(chat_id, message_id),
        },
    }


def inline_callback_update(data: str, update_id: int = 1) -> dict[str, Any]:
    """A tap on a message sent through inline mode: it carries no `message` at all."""
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq{update_id}",
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": "P"},
            "chat_instance": "ci",
            "data": data,
            "inline_message_id": "inline-1",
        },
    }


def text_update(text: str, chat_id: int = CHAT_ID, update_id: int = 1) -> dict[str, Any]:
    return {"update_id": update_id, "message": sent_message(chat_id, 20, text)}


def photo_update(chat_id: int = CHAT_ID, update_id: int = 1) -> dict[str, Any]:
    message = sent_message(chat_id, 21)
    del message["text"]
    message["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    return {"update_id": update_id, "message": message}


def to_update(data: dict[str, Any], bot) -> Update:
    update = Update.de_json(data, bot)
    assert update is not None
    return update
