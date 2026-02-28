"""Tests for Telegram outbox priority queue and edit coalescing."""

import asyncio

import pytest

from auto_dev_loop.telegram.outbox import TelegramOutbox, Priority, OutboxItem
from auto_dev_loop.telegram.models import BotApiError


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


@pytest.mark.asyncio
async def test_retry_after_edit_preserves_coalescing():
    """Edit that hits RetryAfter is re-queued; a subsequent edit for the same
    message coalesces so only one successful call is made with the latest text."""
    from auto_dev_loop.telegram.models import RetryAfter

    # Use a retry_after long enough that we can enqueue a second edit before
    # drain retries, but short enough the test finishes quickly.
    RETRY_DELAY = 0.3

    class RetryOnceClient:
        def __init__(self):
            self.calls = []
            self._edit_count = 0

        async def edit_message_text(self, **kw):
            self._edit_count += 1
            if self._edit_count == 1:
                raise RetryAfter(retry_after=RETRY_DELAY)
            self.calls.append(("edit_message_text", kw))

        async def send_message(self, **kw):
            self.calls.append(("send_message", kw))

            class FakeMsg:
                message_id = len(self.calls)
            return FakeMsg()

    client = RetryOnceClient()
    outbox = TelegramOutbox(client)

    # First edit — drain will dispatch it, hit RetryAfter, and re-queue it.
    await outbox.enqueue_edit(1, 100, "text v1")

    drain_task = asyncio.create_task(outbox.drain_loop())
    # Allow drain to attempt the first dispatch and receive RetryAfter.
    await asyncio.sleep(0.05)

    # At this point the item is back in _pending_edits (with retry_at in the
    # future).  Enqueueing a second edit for the same key coalesces into it.
    await outbox.enqueue_edit(1, 100, "text v2")

    # Wait past the retry window so drain can re-dispatch the coalesced edit.
    await asyncio.sleep(RETRY_DELAY + 0.1)
    drain_task.cancel()

    edit_calls = [c for c in client.calls if c[0] == "edit_message_text"]
    assert len(edit_calls) == 1
    assert edit_calls[0][1]["text"] == "text v2"


@pytest.mark.asyncio
async def test_send_failure_propagates_exception():
    """Exception from send_message is propagated to the returned future."""

    class FailingClient(FakeClient):
        async def send_message(self, **kw):
            raise BotApiError(code=400, description="Bad Request")

    client = FailingClient()
    outbox = TelegramOutbox(client)

    future = await outbox.enqueue_send(1, "test message")
    drain_task = asyncio.create_task(outbox.drain_loop())

    with pytest.raises(BotApiError) as exc_info:
        await asyncio.wait_for(future, timeout=1.0)

    assert exc_info.value.code == 400
    drain_task.cancel()
