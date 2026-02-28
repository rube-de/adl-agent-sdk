"""YAML config loader with environment variable expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .models import (
    Config,
    Defaults,
    RepoConfig,
    TelegramConfig,
    WorkflowSelectionConfig,
)


class ConfigError(Exception):
    pass


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_vars(value: str) -> str:
    """Expand ${VAR} patterns in a string using os.environ."""
    def _replace(match: re.Match) -> str:
        return os.environ.get(match.group(1), "")
    return _ENV_PATTERN.sub(_replace, value)


def _expand_recursive(obj: object) -> object:
    """Recursively expand env vars in strings within dicts/lists."""
    if isinstance(obj, str):
        return expand_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _expand_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_recursive(v) for v in obj]
    return obj


def load_config(path: Path) -> Config:
    """Load and validate config from a YAML file."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    raw = yaml.safe_load(path.read_text())
    raw = _expand_recursive(raw)

    # Parse telegram section
    tg = raw.get("telegram", {})
    try:
        bot_token = tg["bot_token"]
        chat_id = tg["chat_id"]
    except KeyError as e:
        raise ConfigError(f"Missing required telegram config key: {e}") from None
    if not bot_token:
        raise ConfigError("telegram.bot_token must not be empty (check env var expansion)")
    telegram = TelegramConfig(
        bot_token=bot_token,
        chat_id=int(chat_id),
        chat_type=tg.get("chat_type", "private"),
        human_timeout=tg.get("human_timeout", 3600),
        progress_updates=tg.get("progress_updates", True),
    )

    # Parse repos
    repos = []
    for i, r in enumerate(raw.get("repos", [])):
        try:
            repos.append(RepoConfig(
                path=r["path"],
                project_number=r["project_number"],
                columns=r.get("columns", {
                    "source": "Ready for Dev",
                    "in_progress": "In Progress",
                    "done": "Done",
                }),
            ))
        except KeyError as e:
            raise ConfigError(f"Missing required key in repos[{i}]: {e}") from None

    # Parse defaults (merge with Defaults() to preserve unset defaults)
    raw_defaults = raw.get("defaults", {})
    defaults = Defaults(**{
        k: raw_defaults[k]
        for k in Defaults.__dataclass_fields__
        if k in raw_defaults
    })

    # Parse workflow selection
    raw_ws = raw.get("workflow_selection", {})
    workflow_selection = WorkflowSelectionConfig(
        default=raw_ws.get("default", "feature"),
        label_map=raw_ws.get("label_map", {}),
        priority_overrides=raw_ws.get("priority_overrides", {}),
    )

    return Config(
        version=raw.get("version", 3),
        telegram=telegram,
        model_roles=raw.get("model_roles", {}),
        repos=repos,
        defaults=defaults,
        workflow_selection=workflow_selection,
    )
