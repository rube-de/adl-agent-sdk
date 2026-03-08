"""Tests for Telegram per-repo forum topic threading."""

import pytest

from auto_dev_loop.models import TelegramConfig


def test_telegram_config_use_topics_default_false():
    cfg = TelegramConfig(bot_token="tok", chat_id=123)
    assert cfg.use_topics is False


def test_telegram_config_use_topics_enabled():
    cfg = TelegramConfig(bot_token="tok", chat_id=123, use_topics=True)
    assert cfg.use_topics is True
