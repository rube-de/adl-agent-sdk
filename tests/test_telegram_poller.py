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
    poller.on_callback("esc:1:plan", "adl:", handler)
    assert "esc:1:plan" in poller._callback_handlers
    prefix, h = poller._callback_handlers["esc:1:plan"]
    assert prefix == "adl:"
    assert h is handler


def test_reply_handler_registration():
    poller = TelegramPoller(FakeBotApi([]))
    handler = lambda msg: None
    poller.on_reply_to(42, handler)
    assert 42 in poller._reply_handlers


def test_multiple_callback_handlers_same_prefix():
    """Two handlers with the same prefix but different handler_ids both coexist."""
    poller = TelegramPoller(FakeBotApi([]))
    handler_a = lambda cb: None
    handler_b = lambda cb: None

    poller.on_callback("esc:1:plan", "adl:", handler_a)
    poller.on_callback("esc:2:plan", "adl:", handler_b)

    assert "esc:1:plan" in poller._callback_handlers
    assert "esc:2:plan" in poller._callback_handlers
    assert poller._callback_handlers["esc:1:plan"] == ("adl:", handler_a)
    assert poller._callback_handlers["esc:2:plan"] == ("adl:", handler_b)


@pytest.mark.asyncio
async def test_callback_routing_calls_matching_handler():
    """A callback update whose data starts with the registered prefix reaches the handler."""
    from auto_dev_loop.telegram.models import Update, CallbackQuery, Message, Chat, User

    received: list = []

    async def handler(cb: CallbackQuery) -> None:
        received.append(cb.data)

    user = User(id=1, first_name="Test")
    chat = Chat(id=100, type="private")
    msg = Message(message_id=10, chat=chat, text=None)
    cb_query = CallbackQuery(id="q1", from_=user, message=msg, data="adl:approve:42:plan")
    update = Update(update_id=1, callback_query=cb_query)

    poller = TelegramPoller(FakeBotApi([[update]]))
    poller.on_callback("esc:42:plan", "adl:", handler)

    poll_task = asyncio.create_task(poller.poll_loop())
    await asyncio.sleep(0.1)
    poll_task.cancel()

    assert received == ["adl:approve:42:plan"]


def test_removing_one_handler_preserves_others():
    """Popping one handler_id leaves other registrations intact."""
    poller = TelegramPoller(FakeBotApi([]))
    handler_a = lambda cb: None
    handler_b = lambda cb: None

    poller.on_callback("esc:1:plan", "adl:", handler_a)
    poller.on_callback("esc:2:plan", "adl:", handler_b)

    poller._callback_handlers.pop("esc:1:plan")

    assert "esc:1:plan" not in poller._callback_handlers
    assert "esc:2:plan" in poller._callback_handlers
    assert poller._callback_handlers["esc:2:plan"] == ("adl:", handler_b)
