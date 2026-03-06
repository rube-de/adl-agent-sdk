"""Tests for the interactive init wizard."""

from pathlib import Path

import pytest
import typer
import yaml

from auto_dev_loop.config import load_config
from auto_dev_loop.init_wizard import (
    DEFAULT_MODEL_ROLES,
    DEFAULT_TUNABLE_DEFAULTS,
    TELEGRAM_BOT_TOKEN_ENV,
    build_config_data,
    render_config_yaml,
    run_init_wizard,
)


def _patch_prompt_sequence(monkeypatch, values: list[object]) -> None:
    iterator = iter(values)

    def _fake_prompt(*args, **kwargs):
        try:
            return next(iterator)
        except StopIteration:
            raise AssertionError(
                f"_fake_prompt exhausted its values. "
                f"Called with args={args}, kwargs={kwargs}"
            )

    monkeypatch.setattr("auto_dev_loop.init_wizard.typer.prompt", _fake_prompt)


def _patch_confirm_sequence(monkeypatch, values: list[bool]) -> None:
    iterator = iter(values)

    def _fake_confirm(*args, **kwargs):
        try:
            return next(iterator)
        except StopIteration:
            raise AssertionError(
                f"_fake_confirm exhausted its values. "
                f"Called with args={args}, kwargs={kwargs}"
            )

    monkeypatch.setattr("auto_dev_loop.init_wizard.typer.confirm", _fake_confirm)


def test_build_config_data_uses_env_var_reference():
    config_data = build_config_data(
        bot_token="secret-token",
        chat_id=12345,
        chat_type="supergroup",
        use_topics=True,
        use_env_token=True,
        model_roles=dict(DEFAULT_MODEL_ROLES),
        defaults=dict(DEFAULT_TUNABLE_DEFAULTS),
    )

    assert config_data["telegram"]["bot_token"] == f"${{{TELEGRAM_BOT_TOKEN_ENV}}}"
    assert config_data["telegram"]["chat_id"] == 12345
    assert config_data["telegram"]["chat_type"] == "supergroup"
    assert config_data["telegram"]["use_topics"] is True

    rendered = render_config_yaml(config_data)
    parsed = yaml.safe_load(rendered)
    assert parsed["model_roles"] == DEFAULT_MODEL_ROLES
    assert parsed["defaults"]["max_dev_cycles"] == DEFAULT_TUNABLE_DEFAULTS["max_dev_cycles"]


def test_run_init_wizard_declines_overwrite(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: true\n")

    _patch_confirm_sequence(monkeypatch, [False])
    _patch_prompt_sequence(monkeypatch, [])

    with pytest.raises(typer.Exit) as exc_info:
        run_init_wizard(config_path)

    assert exc_info.value.exit_code == 1
    assert config_path.read_text() == "existing: true\n"


def test_run_init_wizard_writes_valid_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"

    _patch_prompt_sequence(monkeypatch, ["token-from-user", 424242])
    _patch_confirm_sequence(monkeypatch, [False, True, True, True])

    output_path = run_init_wizard(config_path)
    assert output_path == config_path
    assert config_path.exists()

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert cfg_yaml["telegram"]["bot_token"] == f"${{{TELEGRAM_BOT_TOKEN_ENV}}}"
    assert cfg_yaml["telegram"]["chat_id"] == 424242
    assert cfg_yaml["telegram"]["chat_type"] == "private"
    assert cfg_yaml["repos"] == []

    monkeypatch.setenv(TELEGRAM_BOT_TOKEN_ENV, "token-from-user")
    cfg = load_config(config_path)
    assert cfg.telegram.chat_id == 424242
    assert cfg.model_roles["default"] == DEFAULT_MODEL_ROLES["default"]


def test_run_init_wizard_group_chat_without_topics(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"

    # negative chat_id, no topics → chat_type should be "group"
    _patch_prompt_sequence(monkeypatch, ["token-from-user", -100123456])
    _patch_confirm_sequence(monkeypatch, [False, True, True, True])

    run_init_wizard(config_path)

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert cfg_yaml["telegram"]["chat_type"] == "group"
    assert cfg_yaml["telegram"]["chat_id"] == -100123456
