"""Layer 2: Rate-limited Telegram client wrapper."""

from __future__ import annotations

import asyncio
import time

from .bot_api import HttpBotClient
from .models import Message


class RateLimiter:
    """Token-bucket rate limiter per chat_id."""

    def __init__(self, rate: float, burst: int = 1):
        self._rate = rate
        self._burst = burst
        self._buckets: dict[int, float] = {}
        self._last_refill: dict[int, float] = {}

    async def acquire(self, chat_id: int) -> None:
        now = time.monotonic()
        if chat_id not in self._buckets:
            self._buckets[chat_id] = self._burst
            self._last_refill[chat_id] = now

        elapsed = now - self._last_refill[chat_id]
        self._buckets[chat_id] = min(
            self._burst,
            self._buckets[chat_id] + elapsed * self._rate,
        )
        self._last_refill[chat_id] = now

        if self._buckets[chat_id] < 1.0:
            wait = (1.0 - self._buckets[chat_id]) / self._rate
            await asyncio.sleep(wait)
            self._buckets[chat_id] = 0.0
            self._last_refill[chat_id] = time.monotonic()
        else:
            self._buckets[chat_id] -= 1.0


class TelegramClient:
    """Rate-limited Telegram client. Same interface as HttpBotClient."""

    def __init__(self, bot_api: HttpBotClient, chat_type: str = "private"):
        self._api = bot_api
        self._limiter = RateLimiter(
            rate=1.0 if chat_type == "private" else 20.0 / 60.0,
        )

    async def send_message(self, chat_id: int, text: str, **kw) -> Message:
        await self._limiter.acquire(chat_id)
        return await self._api.send_message(chat_id, text, **kw)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kw) -> Message:
        await self._limiter.acquire(chat_id)
        return await self._api.edit_message_text(chat_id, message_id, text, **kw)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        await self._limiter.acquire(chat_id)
        return await self._api.delete_message(chat_id, message_id)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        return await self._api.answer_callback_query(callback_query_id, text)
