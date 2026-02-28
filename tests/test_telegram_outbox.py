"""Tests for Telegram outbox priority queue and edit coalescing."""

import asyncio

import pytest

from auto_dev_loop.telegram.outbox import TelegramOutbox, Priority, OutboxItem


class FakeClient:
    """Fake TelegramClient that records calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, **kw):
        self.calls.append(("send_message", kw))

        class FakeMsg:
            message_id = len(self.calls)
        return FakeMsg()

    async def edit_message_text(self, **kw):
        self.calls.append(("edit_message_text", kw))

    async def delete_message(self, **kw):
        self.calls.append(("delete_message", kw))


@pytest.mark.asyncio
async def test_send_priority_over_edit():
    client = FakeClient()
    outbox = TelegramOutbox(client)

    # Enqueue edit first, then send
    await outbox.enqueue_edit(1, 100, "edit text")
    await outbox.enqueue_send(1, "new message")

    # Drain — send (priority 0) should come before edit (priority 2)
    drain_task = asyncio.create_task(outbox.drain_loop())
    await asyncio.sleep(0.1)
    drain_task.cancel()

    assert client.calls[0][0] == "send_message"


@pytest.mark.asyncio
async def test_edit_coalescing():
    client = FakeClient()
    outbox = TelegramOutbox(client)

    # Enqueue 3 edits to same message — only last should be sent
    await outbox.enqueue_edit(1, 100, "text v1")
    await outbox.enqueue_edit(1, 100, "text v2")
    await outbox.enqueue_edit(1, 100, "text v3")

    drain_task = asyncio.create_task(outbox.drain_loop())
    await asyncio.sleep(0.1)
    drain_task.cancel()

    edit_calls = [c for c in client.calls if c[0] == "edit_message_text"]
    assert len(edit_calls) == 1
    assert edit_calls[0][1]["text"] == "text v3"


@pytest.mark.asyncio
async def test_send_returns_future():
    client = FakeClient()
    outbox = TelegramOutbox(client)

    future = await outbox.enqueue_send(1, "hello")
    drain_task = asyncio.create_task(outbox.drain_loop())
    result = await asyncio.wait_for(future, timeout=1.0)
    drain_task.cancel()

    assert result is not None
