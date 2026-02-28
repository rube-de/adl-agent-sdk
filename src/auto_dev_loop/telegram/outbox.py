"""Layer 3: Priority outbox with edit coalescing and RetryAfter backoff."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum

from .models import RetryAfter, BotApiError

log = logging.getLogger(__name__)


class Priority(IntEnum):
    SEND = 0      # New messages have highest priority
    DELETE = 1
    EDIT = 2      # Progress edits are lowest (and coalesced)


@dataclass(order=True)
class OutboxItem:
    priority: Priority
    sequence: int = field(compare=True)
    method: str = field(compare=False)
    kwargs: dict = field(compare=False)
    message_key: str | None = field(compare=False, default=None)
    retry_at: float = field(compare=False, default=0.0)
    future: asyncio.Future | None = field(compare=False, default=None)


class TelegramOutbox:
    """Async priority queue with edit coalescing and RetryAfter backoff."""

    def __init__(self, client):
        self._client = client
        self._queue: asyncio.PriorityQueue[OutboxItem] = asyncio.PriorityQueue()
        self._pending_edits: dict[str, OutboxItem] = {}
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def enqueue_send(self, chat_id: int, text: str, **kw) -> asyncio.Future:
        """Enqueue a new message. Returns Future that resolves to Message."""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(OutboxItem(
            priority=Priority.SEND, sequence=self._next_seq(),
            method="send_message",
            kwargs={"chat_id": chat_id, "text": text, **kw},
            future=future,
        ))
        return future

    async def enqueue_edit(self, chat_id: int, message_id: int, text: str, **kw) -> None:
        """Enqueue an edit. Coalesces with any pending edit for same message."""
        key = f"{chat_id}:{message_id}"
        item = OutboxItem(
            priority=Priority.EDIT, sequence=self._next_seq(),
            method="edit_message_text",
            kwargs={"chat_id": chat_id, "message_id": message_id, "text": text, **kw},
            message_key=key,
        )
        if key in self._pending_edits:
            self._pending_edits[key].kwargs = item.kwargs
        else:
            self._pending_edits[key] = item
            await self._queue.put(item)

    async def enqueue_delete(self, chat_id: int, message_id: int) -> None:
        await self._queue.put(OutboxItem(
            priority=Priority.DELETE, sequence=self._next_seq(),
            method="delete_message",
            kwargs={"chat_id": chat_id, "message_id": message_id},
        ))

    async def drain_loop(self) -> None:
        """Background task: process outbox queue forever."""
        while True:
            item = await self._queue.get()

            # For edits, always use the latest coalesced version
            if item.message_key:
                if item.message_key not in self._pending_edits:
                    continue
                # Honor RetryAfter backoff BEFORE popping so that new
                # enqueue_edit calls arriving during the sleep can still
                # coalesce into _pending_edits[key].
                now = time.monotonic()
                pending = self._pending_edits[item.message_key]
                if pending.retry_at > now:
                    await asyncio.sleep(pending.retry_at - now)
                item = self._pending_edits.pop(item.message_key)
            else:
                # Honor RetryAfter backoff
                now = time.monotonic()
                if item.retry_at > now:
                    await asyncio.sleep(item.retry_at - now)

            try:
                result = await getattr(self._client, item.method)(**item.kwargs)
                if item.future and not item.future.done():
                    item.future.set_result(result)
            except RetryAfter as e:
                item.retry_at = time.monotonic() + e.retry_after
                if item.message_key:
                    self._pending_edits[item.message_key] = item
                await self._queue.put(item)
            except (BotApiError, Exception) as e:
                log.warning(f"Outbox item failed: {e}")
                if item.future and not item.future.done():
                    item.future.set_exception(e)
