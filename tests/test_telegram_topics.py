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
