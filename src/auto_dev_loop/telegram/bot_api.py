"""Layer 1: Raw Telegram Bot API over httpx. No rate limiting, no retry."""

from __future__ import annotations

import httpx
import msgspec

from .models import BotApiResponse, ForumTopic, Message, Update, RetryAfter, BotApiError


class HttpBotClient:
    """Raw Telegram Bot API over httpx. No rate limiting, no retry."""

    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def call(self, method: str, **params) -> BotApiResponse:
        resp = await self._http.post(f"{self._base}/{method}", json=params)
        parsed = msgspec.json.decode(resp.content, type=BotApiResponse)
        if not parsed.ok:
            if parsed.error_code == 429:
                retry_after = parsed.parameters.retry_after if parsed.parameters else 30
                raise RetryAfter(retry_after)
            raise BotApiError(parsed.error_code, parsed.description)
        return parsed

    async def send_message(
        self, chat_id: int, text: str,
        reply_markup: dict | None = None, parse_mode: str = "HTML",
        message_thread_id: int | None = None,
    ) -> Message:
        params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            params["reply_markup"] = reply_markup
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        resp = await self.call("sendMessage", **params)
        return msgspec.json.decode(resp.result, type=Message)

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str,
        reply_markup: dict | None = None, parse_mode: str = "HTML",
    ) -> Message:
        params = {
            "chat_id": chat_id, "message_id": message_id,
            "text": text, "parse_mode": parse_mode,
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        resp = await self.call("editMessageText", **params)
        return msgspec.json.decode(resp.result, type=Message)

    async def create_forum_topic(self, chat_id: int, name: str) -> ForumTopic:
        """Create a forum topic in a supergroup."""
        resp = await self.call("createForumTopic", chat_id=chat_id, name=name[:128])
        return msgspec.json.decode(resp.result, type=ForumTopic)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        return True

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None,
    ) -> bool:
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        await self.call("answerCallbackQuery", **params)
        return True

    async def get_updates(
        self, offset: int | None = None, timeout: int = 50,
    ) -> list[Update]:
        params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        resp = await self.call("getUpdates", **params)
        return msgspec.json.decode(resp.result, type=list[Update])

    async def close(self) -> None:
        await self._http.aclose()
