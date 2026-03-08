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
