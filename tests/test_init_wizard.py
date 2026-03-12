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


# ---- Gap 1: hardcoded token (use_env_token=False) ----


def test_build_config_data_hardcoded_token() -> None:
    test_bot_token = "test-bot-token"  # noqa: S105
    config_data = build_config_data(
        bot_token=test_bot_token,
        chat_id=12345,
        chat_type="private",
        use_topics=False,
        use_env_token=False,
        model_roles=dict(DEFAULT_MODEL_ROLES),
        defaults=dict(DEFAULT_TUNABLE_DEFAULTS),
    )

    assert config_data["telegram"]["bot_token"] == test_bot_token


def test_run_init_wizard_hardcoded_token(tmp_path: Path, monkeypatch, capsys) -> None:
    test_bot_token = "test-bot-token"  # noqa: S105
    config_path = tmp_path / "config.yaml"

    _patch_prompt_sequence(monkeypatch, [test_bot_token, 424242])
    # use_topics=False, use_env_token=False, model_defaults=True, daemon_defaults=True
    _patch_confirm_sequence(monkeypatch, [False, False, True, True])

    run_init_wizard(config_path)

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert cfg_yaml["telegram"]["bot_token"] == test_bot_token

    cfg = load_config(config_path)
    assert cfg.telegram.chat_id == 424242

    captured = capsys.readouterr()
    assert "Config written to" in captured.out
    assert TELEGRAM_BOT_TOKEN_ENV not in captured.out


# ---- Gap 2: validation failure ----


def test_run_init_wizard_validation_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.yaml"

    _patch_prompt_sequence(monkeypatch, ["token", 424242])
    _patch_confirm_sequence(monkeypatch, [False, True, True, True])

    def _boom(*_args, **_kwargs):
        raise Exception("boom")

    monkeypatch.setattr(
        "auto_dev_loop.init_wizard._validate_generated_config", _boom
    )

    with pytest.raises(typer.Exit) as exc_info:
        run_init_wizard(config_path)

    assert exc_info.value.exit_code == 1
    assert not config_path.exists()
    captured = capsys.readouterr()
    assert "Generated config failed validation" in captured.err
    assert "boom" in captured.err


# ---- Gap 3: custom model roles ----


def test_run_init_wizard_custom_model_roles(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"

    # bot_token, chat_id, then 3 custom model names (smol, default, slow)
    _patch_prompt_sequence(
        monkeypatch,
        ["token", 424242, "custom-smol", "custom-default", "custom-slow"],
    )
    # use_topics=False, use_env_token=True, model_defaults=False, daemon_defaults=True
    _patch_confirm_sequence(monkeypatch, [False, True, False, True])

    run_init_wizard(config_path)

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert cfg_yaml["model_roles"] == {"smol": "custom-smol", "default": "custom-default", "slow": "custom-slow"}


# ---- Gap 4: custom daemon defaults ----


def test_run_init_wizard_custom_daemon_defaults(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"

    # bot_token, chat_id, then 5 custom daemon values
    _patch_prompt_sequence(monkeypatch, ["token", 424242, 99, 8, 15, 20, 7])
    # use_topics=False, use_env_token=True, model_defaults=True, daemon_defaults=False
    _patch_confirm_sequence(monkeypatch, [False, True, True, False])

    run_init_wizard(config_path)

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert cfg_yaml["defaults"] == {"poll_interval": 99, "max_concurrent": 8, "max_plan_iterations": 15, "max_dev_cycles": 20, "max_review_cycles": 7}


# ---- Gap 5: overwrite-accept ----


def test_run_init_wizard_overwrite_accept(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: true\n")

    _patch_prompt_sequence(monkeypatch, ["token", 424242])
    # overwrite=True, use_topics=False, use_env_token=True, model_defaults=True, daemon_defaults=True
    _patch_confirm_sequence(monkeypatch, [True, False, True, True, True])

    run_init_wizard(config_path)

    cfg_yaml = yaml.safe_load(config_path.read_text())
    assert "existing" not in cfg_yaml
    assert cfg_yaml["telegram"]["chat_id"] == 424242


# ---- Gap 6: _prompt_required retries on empty input ----


def test_prompt_required_retries_on_empty(monkeypatch) -> None:
    from auto_dev_loop.init_wizard import _prompt_required

    _patch_prompt_sequence(monkeypatch, ["", "   ", "valid-value"])

    result = _prompt_required("test label")
    assert result == "valid-value"
