"""Tests for TelegramBot.escalate() — chat_id verification (issue #3)."""

from __future__ import annotations

import asyncio

import pytest

from auto_dev_loop.models import Issue, TelegramConfig
from auto_dev_loop.telegram import TelegramBot, HumanDecision
from auto_dev_loop.telegram.models import (
    CallbackQuery, Chat, Message, User,
)
from auto_dev_loop.workflow_loader import StageConfig


AUTHORIZED_CHAT_ID = 123
WRONG_CHAT_ID = 999


def _config(chat_id: int = AUTHORIZED_CHAT_ID) -> TelegramConfig:
    return TelegramConfig(bot_token="test-token", chat_id=chat_id, human_timeout=2)


def _issue() -> Issue:
    return Issue(id=42, number=1, repo="test/repo", title="Test issue", body="")


def _stage() -> StageConfig:
    return StageConfig(ref="sec", agent="sec-agent", canVeto=True)


class FakeVerdict:
    feedback = "Something suspicious"
    iteration = 1


def _make_callback(
    *,
    cb_id: str = "q1",
    chat_id: int = AUTHORIZED_CHAT_ID,
    data: str = "adl:approve:42:sec",
    message: Message | None = ...,
) -> CallbackQuery:
    """Build a CallbackQuery with controllable chat_id."""
    user = User(id=1, first_name="Tester")
    if message is ...:
        chat = Chat(id=chat_id, type="private")
        message = Message(message_id=1, chat=chat, text=None)
    return CallbackQuery(id=cb_id, from_=user, message=message, data=data)


class FakeOutbox:
    """Outbox that resolves enqueue_send immediately with a fake Message."""

    async def enqueue_send(self, chat_id: int, text: str, **kw) -> asyncio.Future:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = Message(message_id=1, chat=Chat(id=chat_id, type="private"), text=text)
        future.set_result(msg)
        return future

    async def enqueue_edit(self, *args, **kwargs) -> None:
        pass


class RecordingApi:
    """Fake bot API that records answer_callback_query calls."""

    def __init__(self):
        self.answered: dict[str, str | None] = {}

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        self.answered[callback_query_id] = text
        return True

    async def get_updates(self, offset=None, timeout=50):
        await asyncio.sleep(100)
        return []

    async def close(self):
        pass


async def _get_escalate_handler(bot: TelegramBot, issue: Issue, stage: StageConfig):
    """Start escalate() in background and return the registered callback handler."""
    task = asyncio.create_task(bot.escalate(issue, stage, FakeVerdict(), "security_veto"))
    # Give escalate() time to register the handler
    await asyncio.sleep(0.05)

    handler_id = f"esc:{issue.id}:{stage.ref}"
    _prefix, handler = bot._poller._callback_handlers[handler_id]
    return task, handler


@pytest.mark.asyncio
async def test_callback_from_wrong_chat_is_rejected():
    """Callback from unauthorized chat_id gets 'Unauthorized' and is ignored."""
    api = RecordingApi()
    bot = TelegramBot(_config())
    bot._api = api
    bot._poller._api = api
    bot._outbox = FakeOutbox()

    task, handler = await _get_escalate_handler(bot, _issue(), _stage())

    # Send callback from WRONG chat
    bad_cb = _make_callback(cb_id="bad", chat_id=WRONG_CHAT_ID)
    await handler(bad_cb)

    # Should have answered with "Unauthorized"
    assert api.answered.get("bad") == "Unauthorized"
    # Decision should NOT be resolved
    assert not task.done()

    # Now send from correct chat to let escalate() complete
    good_cb = _make_callback(cb_id="good", chat_id=AUTHORIZED_CHAT_ID)
    await handler(good_cb)
    result = await task
    assert result.action == "approve"


@pytest.mark.asyncio
async def test_callback_with_no_message_is_rejected():
    """Callback with message=None (e.g. very old message) is rejected."""
    api = RecordingApi()
    bot = TelegramBot(_config())
    bot._api = api
    bot._poller._api = api
    bot._outbox = FakeOutbox()

    task, handler = await _get_escalate_handler(bot, _issue(), _stage())

    # Callback with no message attached
    no_msg_cb = _make_callback(cb_id="nomsg", message=None)
    await handler(no_msg_cb)

    assert api.answered.get("nomsg") == "Unauthorized"
    assert not task.done()

    # Clean up — cancel the escalate task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_callback_from_authorized_chat_is_accepted():
    """Callback from the configured chat_id resolves the escalation."""
    api = RecordingApi()
    bot = TelegramBot(_config())
    bot._api = api
    bot._poller._api = api
    bot._outbox = FakeOutbox()

    task, handler = await _get_escalate_handler(bot, _issue(), _stage())

    cb = _make_callback(cb_id="ok", chat_id=AUTHORIZED_CHAT_ID)
    await handler(cb)

    result = await task
    assert result.action == "approve"
    # Should have answered with the action, not "Unauthorized"
    assert api.answered.get("ok") != "Unauthorized"
