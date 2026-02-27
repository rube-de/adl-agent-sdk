"""Tests for YAML config loader."""

import os
from pathlib import Path

import pytest

from auto_dev_loop.config import load_config, expand_env_vars, ConfigError
from auto_dev_loop.models import Config, TelegramConfig, RepoConfig


MINIMAL_CONFIG = """\
version: 3

telegram:
  bot_token: "test-token"
  chat_id: 12345

model_roles:
  smol: claude-haiku-4-5
  default: claude-sonnet-4-5
  slow: claude-opus-4-5

repos:
  - path: /tmp/test-repo
    project_number: 1
"""

CONFIG_WITH_ENV = """\
version: 3

telegram:
  bot_token: "${TEST_BOT_TOKEN}"
  chat_id: 99999

model_roles:
  smol: haiku
  default: sonnet
  slow: opus

repos:
  - path: /tmp/repo
    project_number: 1
"""


def test_load_minimal_config(tmp_config_file: Path):
    tmp_config_file.write_text(MINIMAL_CONFIG)
    cfg = load_config(tmp_config_file)
    assert isinstance(cfg, Config)
    assert isinstance(cfg.telegram, TelegramConfig)
    assert cfg.telegram.bot_token == "test-token"
    assert cfg.telegram.chat_id == 12345
    assert cfg.model_roles["smol"] == "claude-haiku-4-5"
    assert len(cfg.repos) == 1
    assert isinstance(cfg.repos[0], RepoConfig)
    assert cfg.repos[0].path == "/tmp/test-repo"


def test_load_config_defaults(tmp_config_file: Path):
    tmp_config_file.write_text(MINIMAL_CONFIG)
    cfg = load_config(tmp_config_file)
    assert cfg.defaults.poll_interval == 60
    assert cfg.defaults.max_dev_cycles == 5
    assert cfg.defaults.external_reviewers == ["gemini"]


def test_env_var_expansion(monkeypatch):
    monkeypatch.setenv("TEST_BOT_TOKEN", "expanded-token")
    result = expand_env_vars("${TEST_BOT_TOKEN}")
    assert result == "expanded-token"


def test_env_var_expansion_missing():
    result = expand_env_vars("${DEFINITELY_NOT_SET_12345}")
    assert result == ""


def test_load_config_with_env_vars(tmp_config_file: Path, monkeypatch):
    monkeypatch.setenv("TEST_BOT_TOKEN", "env-token-value")
    tmp_config_file.write_text(CONFIG_WITH_ENV)
    cfg = load_config(tmp_config_file)
    assert cfg.telegram.bot_token == "env-token-value"


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config(Path("/nonexistent/config.yaml"))


def test_load_config_with_workflow_selection(tmp_config_file: Path):
    config_text = MINIMAL_CONFIG + """
workflow_selection:
  default: feature
  label_map:
    bug: bug_fix
    docs: documentation
"""
    tmp_config_file.write_text(config_text)
    cfg = load_config(tmp_config_file)
    assert cfg.workflow_selection.default == "feature"
    assert cfg.workflow_selection.label_map["bug"] == "bug_fix"


def test_load_config_with_custom_defaults(tmp_config_file: Path):
    config_text = MINIMAL_CONFIG + """
defaults:
  poll_interval: 120
  max_dev_cycles: 10
"""
    tmp_config_file.write_text(config_text)
    cfg = load_config(tmp_config_file)
    assert cfg.defaults.poll_interval == 120
    assert cfg.defaults.max_dev_cycles == 10
    # Unset defaults should still have their default values
    assert cfg.defaults.max_plan_iterations == 3
