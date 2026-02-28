"""Tests for Telegram long-polling and callback routing."""

import asyncio

import pytest

from auto_dev_loop.telegram.poller import TelegramPoller


class FakeBotApi:
    """Fake bot API returning predetermined updates."""

    def __init__(self, updates):
        self._updates = updates
        self._call_count = 0

    async def get_updates(self, offset=None, timeout=50):
        if self._call_count < len(self._updates):
            batch = self._updates[self._call_count]
            self._call_count += 1
            return batch
        await asyncio.sleep(100)
        return []


def test_callback_handler_registration():
    poller = TelegramPoller(FakeBotApi([]))
    handler = lambda cb: None
    poller.on_callback("adl:", handler)
    assert "adl:" in poller._callback_handlers


def test_reply_handler_registration():
    poller = TelegramPoller(FakeBotApi([]))
    handler = lambda msg: None
    poller.on_reply_to(42, handler)
    assert 42 in poller._reply_handlers
