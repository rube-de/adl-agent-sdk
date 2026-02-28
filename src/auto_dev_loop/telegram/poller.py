"""Layer 4: Long-polling + callback routing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from .models import CallbackQuery, Message, Update

log = logging.getLogger(__name__)

CallbackHandler = Callable[[CallbackQuery], Awaitable[None]]
ReplyHandler = Callable[[Message], Awaitable[None]]


class TelegramPoller:
    """Long-polling loop. Routes callbacks and replies to handlers."""

    def __init__(self, bot_api):
        self._api = bot_api
        self._offset: int = 0
        self._seen: set[int] = set()
        # Keyed by unique handler_id; value is (prefix, handler).
        self._callback_handlers: dict[str, tuple[str, CallbackHandler]] = {}
        self._reply_handlers: dict[int, ReplyHandler] = {}

    def on_callback(self, handler_id: str, prefix: str, handler: CallbackHandler) -> None:
        """Register handler for callback_data starting with prefix.

        handler_id must be unique per registration (e.g. 'esc:42:plan').
        Multiple handlers can share the same prefix.
        """
        self._callback_handlers[handler_id] = (prefix, handler)

    def on_reply_to(self, message_id: int, handler: ReplyHandler) -> None:
        """Register one-shot handler for replies to a specific message."""
        self._reply_handlers[message_id] = handler

    async def poll_loop(self) -> None:
        """Long-poll Telegram for updates. Runs forever."""
        while True:
            try:
                updates = await self._api.get_updates(
                    offset=self._offset, timeout=50,
                )
            except (httpx.HTTPError, OSError):
                log.warning("Telegram poll failed, retrying in 5s")
                await asyncio.sleep(5)
                continue

            for update in updates:
                self._offset = update.update_id + 1

                if update.update_id in self._seen:
                    continue
                self._seen.add(update.update_id)
                if len(self._seen) > 10_000:
                    self._seen = set(sorted(self._seen)[-5_000:])

                if update.callback_query:
                    await self._route_callback(update.callback_query)
                elif update.message and update.message.reply_to_message:
                    await self._route_reply(update.message)

    async def _route_callback(self, cb: CallbackQuery) -> None:
        if not cb.data:
            return
        for handler_id, (prefix, handler) in self._callback_handlers.items():
            if cb.data.startswith(prefix):
                try:
                    await handler(cb)
                except Exception:
                    log.exception(f"Callback handler error: {cb.data}")
                return

    async def _route_reply(self, msg: Message) -> None:
        reply_to_id = msg.reply_to_message.message_id
        handler = self._reply_handlers.pop(reply_to_id, None)
        if handler:
            try:
                await handler(msg)
            except Exception:
                log.exception(f"Reply handler error: reply to {reply_to_id}")
