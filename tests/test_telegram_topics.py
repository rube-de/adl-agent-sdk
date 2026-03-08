"""Tests for Telegram per-repo forum topic threading."""

import pytest

from auto_dev_loop.models import TelegramConfig


def test_telegram_config_use_topics_default_false():
    cfg = TelegramConfig(bot_token="tok", chat_id=123)
    assert cfg.use_topics is False


def test_telegram_config_use_topics_enabled():
    cfg = TelegramConfig(bot_token="tok", chat_id=123, use_topics=True)
    assert cfg.use_topics is True


from pathlib import Path
from auto_dev_loop.config import load_config


@pytest.fixture
def config_yaml(tmp_path):
    """Create a minimal config YAML file."""
    def _make(extra_telegram=""):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(f"""\
version: 3
telegram:
  bot_token: "test-token"
  chat_id: -100123456789
  chat_type: group
  {extra_telegram}
model_roles:
  worker: "claude-sonnet-4-20250514"
repos:
  - path: /tmp/repo
    project_number: 1
""")
        return cfg
    return _make


def test_config_parses_use_topics_true(config_yaml):
    cfg = load_config(config_yaml("use_topics: true"))
    assert cfg.telegram.use_topics is True


def test_config_parses_use_topics_default_false(config_yaml):
    cfg = load_config(config_yaml(""))
    assert cfg.telegram.use_topics is False


import asyncio
from unittest.mock import AsyncMock, patch

import msgspec

from auto_dev_loop.telegram.bot_api import HttpBotClient
from auto_dev_loop.telegram.models import BotApiResponse, Chat, Message


@pytest.fixture
def bot_client():
    return HttpBotClient("fake-token")


@pytest.mark.asyncio
async def test_create_forum_topic(bot_client):
    mock_resp = BotApiResponse(
        ok=True,
        result=msgspec.json.encode({"message_thread_id": 999, "name": "owner/repo"}),
    )
    with patch.object(bot_client, "call", new_callable=AsyncMock, return_value=mock_resp):
        result = await bot_client.create_forum_topic(chat_id=-100123, name="owner/repo")
        assert result["message_thread_id"] == 999
        bot_client.call.assert_awaited_once_with(
            "createForumTopic", chat_id=-100123, name="owner/repo",
        )


@pytest.mark.asyncio
async def test_send_message_with_thread_id(bot_client):
    mock_msg = Message(message_id=1, chat=Chat(id=-100123, type="supergroup"))
    mock_resp = BotApiResponse(
        ok=True,
        result=msgspec.json.encode(mock_msg),
    )
    with patch.object(bot_client, "call", new_callable=AsyncMock, return_value=mock_resp):
        await bot_client.send_message(
            chat_id=-100123, text="hello", message_thread_id=999,
        )
        call_kwargs = bot_client.call.call_args
        assert call_kwargs.kwargs["message_thread_id"] == 999


@pytest.mark.asyncio
async def test_send_message_without_thread_id(bot_client):
    mock_msg = Message(message_id=1, chat=Chat(id=123, type="private"))
    mock_resp = BotApiResponse(
        ok=True,
        result=msgspec.json.encode(mock_msg),
    )
    with patch.object(bot_client, "call", new_callable=AsyncMock, return_value=mock_resp):
        await bot_client.send_message(chat_id=123, text="hello")
        call_kwargs = bot_client.call.call_args
        assert "message_thread_id" not in call_kwargs.kwargs


@pytest.mark.asyncio
async def test_edit_message_text_with_thread_id(bot_client):
    mock_msg = Message(message_id=1, chat=Chat(id=-100123, type="supergroup"))
    mock_resp = BotApiResponse(
        ok=True,
        result=msgspec.json.encode(mock_msg),
    )
    with patch.object(bot_client, "call", new_callable=AsyncMock, return_value=mock_resp):
        await bot_client.edit_message_text(
            chat_id=-100123, message_id=1, text="updated", message_thread_id=999,
        )
        call_kwargs = bot_client.call.call_args
        assert call_kwargs.kwargs["message_thread_id"] == 999


from auto_dev_loop.telegram import TelegramBot, HumanDecision
from auto_dev_loop.state import StateStore
from auto_dev_loop.models import Issue


@pytest.fixture
async def state_store(tmp_path):
    store = StateStore(tmp_path / "test.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def topics_config():
    return TelegramConfig(
        bot_token="fake", chat_id=-100123, chat_type="supergroup",
        use_topics=True,
    )


@pytest.fixture
def no_topics_config():
    return TelegramConfig(
        bot_token="fake", chat_id=-100123, chat_type="supergroup",
        use_topics=False,
    )


@pytest.fixture
def sample_issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="")


@pytest.mark.asyncio
async def test_resolve_thread_creates_topic_on_first_call(topics_config, state_store, sample_issue):
    bot = TelegramBot(topics_config, store=state_store)
    with patch.object(
        bot._api, "create_forum_topic",
        new_callable=AsyncMock,
        return_value={"message_thread_id": 555, "name": "owner/repo"},
    ):
        thread_id = await bot._resolve_thread_id(sample_issue.repo)
    assert thread_id == 555
    stored = await state_store.get_thread_id("owner/repo")
    assert stored == 555


@pytest.mark.asyncio
async def test_resolve_thread_reuses_stored_id(topics_config, state_store, sample_issue):
    await state_store.store_thread_id("owner/repo", 888)
    bot = TelegramBot(topics_config, store=state_store)
    thread_id = await bot._resolve_thread_id(sample_issue.repo)
    assert thread_id == 888


@pytest.mark.asyncio
async def test_resolve_thread_returns_none_when_topics_disabled(no_topics_config, state_store, sample_issue):
    bot = TelegramBot(no_topics_config, store=state_store)
    thread_id = await bot._resolve_thread_id(sample_issue.repo)
    assert thread_id is None


@pytest.mark.asyncio
async def test_notify_completion_sends_to_thread(topics_config, state_store, sample_issue):
    await state_store.store_thread_id("owner/repo", 555)
    bot = TelegramBot(topics_config, store=state_store)
    with patch.object(bot._outbox, "enqueue_send", new_callable=AsyncMock) as mock_send:
        mock_future = asyncio.get_event_loop().create_future()
        mock_future.set_result(None)
        mock_send.return_value = mock_future
        await bot.notify_completion(sample_issue, "https://github.com/pr/1")
    call_kwargs = mock_send.call_args
    assert call_kwargs.kwargs.get("message_thread_id") == 555


@pytest.mark.asyncio
async def test_notify_completion_no_thread_when_disabled(no_topics_config, state_store, sample_issue):
    bot = TelegramBot(no_topics_config, store=state_store)
    with patch.object(bot._outbox, "enqueue_send", new_callable=AsyncMock) as mock_send:
        mock_future = asyncio.get_event_loop().create_future()
        mock_future.set_result(None)
        mock_send.return_value = mock_future
        await bot.notify_completion(sample_issue, "https://github.com/pr/1")
    call_kwargs = mock_send.call_args
    assert "message_thread_id" not in call_kwargs.kwargs


@pytest.mark.asyncio
async def test_telegram_client_forwards_message_thread_id(bot_client):
    """TelegramClient (rate-limited wrapper) must forward message_thread_id to HttpBotClient."""
    from auto_dev_loop.telegram.client import TelegramClient

    mock_msg = Message(message_id=42, chat=Chat(id=-100123, type="supergroup"))
    mock_resp = BotApiResponse(
        ok=True,
        result=msgspec.json.encode(mock_msg),
    )
    client = TelegramClient(bot_api=bot_client, chat_type="supergroup")
    with patch.object(bot_client, "call", new_callable=AsyncMock, return_value=mock_resp):
        await client.send_message(chat_id=-100123, text="hi", message_thread_id=777)
        call_kwargs = bot_client.call.call_args
        assert call_kwargs.kwargs["message_thread_id"] == 777


@pytest.mark.asyncio
async def test_full_round_trip_topic_creation_and_reuse(topics_config, state_store, sample_issue):
    """Integration: first message creates topic, second reuses it."""
    bot = TelegramBot(topics_config, store=state_store)

    mock_msg = Message(message_id=10, chat=Chat(id=-100123, type="supergroup"))
    mock_future = asyncio.get_event_loop().create_future()
    mock_future.set_result(mock_msg)

    with (
        patch.object(
            bot._api, "create_forum_topic",
            new_callable=AsyncMock,
            return_value={"message_thread_id": 777, "name": "owner/repo"},
        ) as mock_create,
        patch.object(
            bot._outbox, "enqueue_send",
            new_callable=AsyncMock,
            return_value=mock_future,
        ) as mock_send,
    ):
        # First call — creates topic
        await bot.notify_completion(sample_issue, "https://github.com/pr/1")
        assert mock_create.await_count == 1
        assert mock_send.call_args.kwargs["message_thread_id"] == 777

        # Second call — reuses cached thread ID (no new topic created)
        mock_future2 = asyncio.get_event_loop().create_future()
        mock_future2.set_result(None)
        mock_send.return_value = mock_future2
        await bot.notify_error(sample_issue, "oops")
        assert mock_create.await_count == 1  # still 1, not 2
        assert mock_send.call_args.kwargs["message_thread_id"] == 777

    # Verify DB persistence
    stored = await state_store.get_thread_id("owner/repo")
    assert stored == 777


@pytest.mark.asyncio
async def test_topics_disabled_sends_no_thread_id(no_topics_config, state_store, sample_issue):
    """When use_topics=False, no message_thread_id is sent."""
    bot = TelegramBot(no_topics_config, store=state_store)

    mock_future = asyncio.get_event_loop().create_future()
    mock_future.set_result(None)

    with patch.object(bot._outbox, "enqueue_send", new_callable=AsyncMock, return_value=mock_future) as mock_send:
        await bot.notify_completion(sample_issue, "https://github.com/pr/1")
    assert "message_thread_id" not in mock_send.call_args.kwargs
