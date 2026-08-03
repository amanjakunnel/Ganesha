from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class TelegramTransport(Protocol):
    """Transport boundary for Telegram Bot API calls (HTTP or in-memory tests)."""

    def post(self, method: str, data: dict[str, Any] | None = None) -> Any:
        """Return the Telegram API `result` payload, or None on failure."""


@dataclass
class HttpTelegramTransport:
    """Production transport using urllib against api.telegram.org."""

    token: str

    def __post_init__(self) -> None:
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def post(self, method: str, data: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{method}"
        req_data = None
        headers: dict[str, str] = {}
        if data is not None:
            req_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                if not res_json.get("ok"):
                    logger.error("Telegram API error in %s: %s", method, res_json)
                    return None
                return res_json.get("result")
        except urllib.error.URLError as exc:
            logger.error("HTTP request to Telegram failed for %s: %s", method, exc)
            return None


@dataclass
class RecordingTelegramTransport:
    """In-memory transport for unit tests; records calls and returns canned results."""

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    def post(self, method: str, data: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, data))
        if method in self.responses:
            return self.responses[method]
        if method == "sendMessage":
            chat_id = (data or {}).get("chat_id")
            return {"message_id": 999, "chat": {"id": chat_id}, "text": (data or {}).get("text")}
        if method == "answerCallbackQuery":
            return True
        if method == "editMessageText":
            chat_id = (data or {}).get("chat_id")
            msg_id = (data or {}).get("message_id")
            return {"message_id": msg_id, "chat": {"id": chat_id}, "text": (data or {}).get("text")}
        if method == "getUpdates":
            return self.responses.get("getUpdates", [])
        return None


class TelegramBotClient:
    """Synchronous Telegram Bot API client."""

    def __init__(self, transport: TelegramTransport) -> None:
        self._transport = transport

    @classmethod
    def from_token(cls, token: str) -> TelegramBotClient:
        return cls(HttpTelegramTransport(token))

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        data: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        res = self._transport.post("getUpdates", data)
        return res if isinstance(res, list) else []

    def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        res = self._transport.post("sendMessage", payload)
        return res if isinstance(res, dict) else None

    def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> bool | None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
            payload["show_alert"] = show_alert
        res = self._transport.post("answerCallbackQuery", payload)
        return res if isinstance(res, bool) else None

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        res = self._transport.post("editMessageText", payload)
        return res if isinstance(res, dict) else None


def is_authorized(
    user_id: int | None,
    chat_id: int | None,
    allowed_user_id: int | None,
    allowed_chat_id: int | None,
) -> bool:
    """Verify if user and/or chat is authorized based on environment constraints."""
    if allowed_user_id is not None and user_id != allowed_user_id:
        return False
    if allowed_chat_id is not None and chat_id != allowed_chat_id:
        return False
    return True


def extract_identity_from_update(update: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return (user_id, chat_id) from a Telegram update if present."""
    if "message" in update:
        msg = update["message"]
        user_id = (msg.get("from") or {}).get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        return user_id, chat_id
    if "callback_query" in update:
        cb = update["callback_query"]
        user_id = (cb.get("from") or {}).get("id")
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
        return user_id, chat_id
    return None, None
