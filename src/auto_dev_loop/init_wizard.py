"""Interactive setup wizard for creating ``~/.adl/config.yaml``."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import typer
import yaml

from ._paths import ADL_HOME
from .config import load_config
from .models import Defaults

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

DEFAULT_MODEL_ROLES = {
    "smol": "claude-haiku-4-5",
    "default": "claude-sonnet-4-5",
    "slow": "claude-opus-4-5",
}

_DEFAULTS = Defaults()
DEFAULT_TUNABLE_DEFAULTS = {
    "poll_interval": _DEFAULTS.poll_interval,
    "max_concurrent": _DEFAULTS.max_concurrent,
    "max_plan_iterations": _DEFAULTS.max_plan_iterations,
    "max_dev_cycles": _DEFAULTS.max_dev_cycles,
    "max_review_cycles": _DEFAULTS.max_review_cycles,
}


def _prompt_required(label: str, *, hide_input: bool = False) -> str:
    """Prompt until the user provides a non-empty value."""
    while True:
        value = typer.prompt(label, hide_input=hide_input).strip()
        if value:
            return value
        typer.echo("Value cannot be empty.")


def _prompt_telegram() -> tuple[str, int, str, bool, bool]:
    typer.echo("Telegram setup")
    typer.echo("Create a bot via @BotFather and paste the bot token below.")
    bot_token = _prompt_required("Telegram bot token", hide_input=True)
    typer.echo(
        "Find chat_id via @userinfobot or by calling getUpdates on your bot and reading chat.id."
    )
    chat_id = typer.prompt("Telegram chat_id", type=int)
    use_topics = typer.confirm(
        "Is this a group chat with Topics enabled?",
        default=False,
    )
    if chat_id > 0:
        chat_type = "private"
    elif use_topics:
        chat_type = "supergroup"
    else:
        chat_type = "group"
    use_env_token = typer.confirm(
        f"Store bot token as ${{{TELEGRAM_BOT_TOKEN_ENV}}} instead of hardcoding it?",
        default=True,
    )
    return bot_token, chat_id, chat_type, use_topics, use_env_token


def _prompt_model_roles() -> dict[str, str]:
    typer.echo("")
    typer.echo("Model roles")
    for role, model in DEFAULT_MODEL_ROLES.items():
        typer.echo(f"  {role}: {model}")

    if typer.confirm("Use these model role defaults?", default=True):
        return dict(DEFAULT_MODEL_ROLES)

    model_roles = dict(DEFAULT_MODEL_ROLES)
    for role, current in model_roles.items():
        model_roles[role] = _prompt_required(f"Model for {role} role (current: {current})")
    return model_roles


def _prompt_defaults() -> dict[str, int]:
    typer.echo("")
    typer.echo("Daemon defaults")
    for key, value in DEFAULT_TUNABLE_DEFAULTS.items():
        typer.echo(f"  {key}: {value}")

    if typer.confirm("Use these daemon defaults?", default=True):
        return dict(DEFAULT_TUNABLE_DEFAULTS)

    values = dict(DEFAULT_TUNABLE_DEFAULTS)
    for key, current in values.items():
        values[key] = typer.prompt(f"{key}", default=current, type=int)
    return values


def build_config_data(
    *,
    bot_token: str,
    chat_id: int,
    chat_type: str,
    use_topics: bool,
    use_env_token: bool,
    model_roles: dict[str, str],
    defaults: dict[str, int],
) -> dict[str, Any]:
    """Build the final config document from wizard answers."""
    telegram: dict[str, Any] = {
        "bot_token": (
            f"${{{TELEGRAM_BOT_TOKEN_ENV}}}" if use_env_token else bot_token
        ),
        "chat_id": chat_id,
        "chat_type": chat_type,
    }
    if use_topics:
        # Forward ref: consumed by #27 (Telegram topic-per-repo threading)
        telegram["use_topics"] = True

    return {
        "version": 3,
        "telegram": telegram,
        "model_roles": dict(model_roles),
        "defaults": dict(defaults),
        "repos": [],
    }


def render_config_yaml(config_data: dict[str, Any]) -> str:
    """Render config data as YAML."""
    return yaml.safe_dump(config_data, sort_keys=False)


def _validate_generated_config(config_data: dict[str, Any], *, bot_token: str) -> None:
    """Validate generated YAML by loading it through the real config loader."""
    cfg_text = render_config_yaml(config_data)
    temp_path: Path | None = None

    previous_env_value = os.environ.get(TELEGRAM_BOT_TOKEN_ENV)
    os.environ[TELEGRAM_BOT_TOKEN_ENV] = bot_token

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(cfg_text)
            temp_path = Path(handle.name)
        load_config(temp_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if previous_env_value is not None:
            os.environ[TELEGRAM_BOT_TOKEN_ENV] = previous_env_value
        else:
            os.environ.pop(TELEGRAM_BOT_TOKEN_ENV, None)


def run_init_wizard(config_path: Path | None = None) -> Path:
    """Run the one-time init wizard and write config.yaml."""
    path = config_path or (ADL_HOME / "config.yaml")

    if path.exists():
        overwrite = typer.confirm(
            f"{path} already exists. Overwrite?",
            default=False,
        )
        if not overwrite:
            typer.echo("Init cancelled. Existing config was not modified.")
            raise typer.Exit(1)

    bot_token, chat_id, chat_type, use_topics, use_env_token = _prompt_telegram()
    model_roles = _prompt_model_roles()
    defaults = _prompt_defaults()

    config_data = build_config_data(
        bot_token=bot_token,
        chat_id=chat_id,
        chat_type=chat_type,
        use_topics=use_topics,
        use_env_token=use_env_token,
        model_roles=model_roles,
        defaults=defaults,
    )

    try:
        _validate_generated_config(config_data, bot_token=bot_token)
    except Exception as exc:
        typer.echo(f"Generated config failed validation: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_config_yaml(config_data))
    except OSError as exc:
        typer.echo(
            f"Failed to write config to {path}: {exc}\n"
            f"Try again with --config <path>.",
            err=True,
        )
        raise typer.Exit(1) from exc

    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        typer.echo(
            f"Warning: could not set restrictive permissions on {path}.",
            err=True,
        )

    typer.echo(f"Config written to {path}")
    if use_env_token:
        typer.echo(f"Set {TELEGRAM_BOT_TOKEN_ENV} before running `adl validate`.")
    typer.echo("Next steps:")
    typer.echo("  1. Edit the generated config to add your repositories under `repos:`.")
    typer.echo("  2. Run `adl validate` to check the configuration.")
    typer.echo("  3. Run `adl run` to start the auto dev loop.")

    return path
