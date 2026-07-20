from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TelegramBotClient:
    """Synchronous Telegram Bot API client using standard library urllib."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def _request(self, method: str, data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{method}"
        req_data = None
        headers = {}
        if data is not None:
            req_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                if not res_json.get("ok"):
                    logger.error(f"Telegram API error in {method}: {res_json}")
                    return None
                return res_json.get("result")
        except urllib.error.URLError as e:
            logger.error(f"HTTP request to Telegram failed for {method}: {e}")
            return None

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        data: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        res = self._request("getUpdates", data)
        return res if isinstance(res, list) else []

    def send_message(
        self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        res = self._request("sendMessage", data)
        return res if isinstance(res, dict) else None

    def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
    ) -> Optional[bool]:
        data: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
        }
        if text is not None:
            data["text"] = text
            data["show_alert"] = show_alert
        res = self._request("answerCallbackQuery", data)
        return res if isinstance(res, bool) else None

    def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        res = self._request("editMessageText", data)
        return res if isinstance(res, dict) else None


def is_authorized(
    user_id: Optional[int],
    chat_id: Optional[int],
    allowed_user_id: Optional[int],
    allowed_chat_id: Optional[int],
) -> bool:
    """Verify if user and/or chat is authorized based on environment constraints."""
    if allowed_user_id is not None and user_id != allowed_user_id:
        return False
    if allowed_chat_id is not None and chat_id != allowed_chat_id:
        return False
    return True
