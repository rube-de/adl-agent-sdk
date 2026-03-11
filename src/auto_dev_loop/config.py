"""YAML config loader with environment variable expansion."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from .models import (
    Config,
    Defaults,
    RepoConfig,
    ResolvedRepoConfig,
    TelegramConfig,
    WorkflowSelectionConfig,
)


log = logging.getLogger(__name__)


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
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file must contain a YAML mapping, got {type(raw).__name__}"
        )
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
        use_topics=tg.get("use_topics", False),
    )

    # Parse repos
    repos = []
    for i, r in enumerate(raw.get("repos", [])):
        try:
            if not isinstance(r, dict):
                raise ConfigError(
                    f"repos[{i}] must be a mapping, got {type(r).__name__}"
                )
            _ALLOWED_REPO_KEYS = {
                "path", "project_number", "owner", "repo", "columns",
                "agents_dir", "workflows_dir", "defaults",
                "workflow_selection", "model_roles",
            }
            unexpected = set(r) - _ALLOWED_REPO_KEYS
            if unexpected:
                raise ConfigError(
                    f"Unrecognized key(s) in repos[{i}]: "
                    f"{', '.join(sorted(unexpected))}"
                )
            kwargs: dict = {
                "path": r["path"],
                "project_number": r["project_number"],
                "owner": r.get("owner"),
                "repo": r.get("repo"),
            }
            if "columns" in r:
                kwargs["columns"] = r["columns"]
            # Per-repo overrides (stored as raw dicts for merge)
            if "agents_dir" in r:
                kwargs["agents_dir"] = r["agents_dir"]
            if "workflows_dir" in r:
                kwargs["workflows_dir"] = r["workflows_dir"]
            if "defaults" in r:
                kwargs["defaults"] = r["defaults"]
            if "workflow_selection" in r:
                kwargs["workflow_selection"] = r["workflow_selection"]
            if "model_roles" in r:
                kwargs["model_roles"] = r["model_roles"]
            repos.append(RepoConfig(**kwargs))
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


def resolve_repo_config(repo: RepoConfig, global_cfg: Config) -> ResolvedRepoConfig:
    """Merge per-repo overrides with global config. Pure function.

    Resolution order:
    1. Start with global values
    2. Repo-level values override global (shallow merge for dicts)
    3. For nested dicts (label_map, model_roles), repo keys override
       global keys with the same name
    4. priority_overrides are deep-merged per priority key — repo labels
       override matching global labels without dropping unrelated ones

    List-valued defaults (``review_backoff``, ``external_reviewers``, etc.)
    are replaced wholesale by the repo override — not appended/merged.
    """
    # --- model_roles: shallow dict merge ---
    if repo.model_roles is not None:
        if not isinstance(repo.model_roles, dict):
            raise ConfigError(
                f"Per-repo model_roles must be a mapping, "
                f"got {type(repo.model_roles).__name__} in {repo.path}"
            )
        for role, model_id in repo.model_roles.items():
            if not isinstance(role, str) or not isinstance(model_id, str):
                raise ConfigError(
                    f"Per-repo model_roles entries must map strings to strings "
                    f"in {repo.path}"
                )
        merged_mr = {**global_cfg.model_roles, **repo.model_roles}
    else:
        merged_mr = dict(global_cfg.model_roles)

    # --- workflow_selection: field-level + dict merge ---
    gws = global_cfg.workflow_selection
    if repo.workflow_selection is not None:
        if not isinstance(repo.workflow_selection, dict):
            raise ConfigError(
                f"Per-repo workflow_selection must be a mapping, "
                f"got {type(repo.workflow_selection).__name__} in {repo.path}"
            )
        rws = repo.workflow_selection
        _KNOWN_WS_KEYS = {"default", "label_map", "priority_overrides"}
        for key in rws:
            if key not in _KNOWN_WS_KEYS:
                log.warning(
                    "Ignoring unrecognized per-repo workflow_selection key %r in %s "
                    "(valid keys: %s)",
                    key, repo.path, ", ".join(sorted(_KNOWN_WS_KEYS)),
                )
        repo_default = rws.get("default", gws.default)
        if not isinstance(repo_default, str):
            raise ConfigError(
                f"Per-repo workflow_selection.default must be a string, "
                f"got {type(repo_default).__name__} in {repo.path}"
            )
        repo_label_map = rws.get("label_map", {})
        if not isinstance(repo_label_map, dict):
            raise ConfigError(
                f"Per-repo workflow_selection.label_map must be a mapping, "
                f"got {type(repo_label_map).__name__} in {repo.path}"
            )
        for label_key, label_val in repo_label_map.items():
            if not isinstance(label_val, str):
                raise ConfigError(
                    f"Per-repo workflow_selection.label_map[{label_key!r}] "
                    f"must be a string, got {type(label_val).__name__} in {repo.path}"
                )
        repo_priority = rws.get("priority_overrides", {})
        if not isinstance(repo_priority, dict):
            raise ConfigError(
                f"Per-repo workflow_selection.priority_overrides must be a mapping, "
                f"got {type(repo_priority).__name__} in {repo.path}"
            )
        for prio_key, prio_val in repo_priority.items():
            if not isinstance(prio_val, dict):
                raise ConfigError(
                    f"Per-repo workflow_selection.priority_overrides[{prio_key!r}] "
                    f"must be a mapping, got {type(prio_val).__name__} in {repo.path}"
                )
        # Deep-merge priority_overrides per priority key so repo overrides
        # only replace matching labels, not the entire priority bucket.
        merged_po = {k: dict(v) for k, v in gws.priority_overrides.items()}
        for prio_key, prio_val in repo_priority.items():
            if prio_key in merged_po:
                merged_po[prio_key].update(prio_val)
            else:
                merged_po[prio_key] = dict(prio_val)
        merged_ws = WorkflowSelectionConfig(
            default=rws.get("default", gws.default),
            label_map={**gws.label_map, **repo_label_map},
            priority_overrides=merged_po,
        )
    else:
        merged_ws = WorkflowSelectionConfig(
            default=gws.default,
            label_map=dict(gws.label_map),
            priority_overrides={k: dict(v) for k, v in gws.priority_overrides.items()},
        )

    # --- defaults: shallow dict merge ---
    # Copy mutable fields (lists) so the resolved config is independent
    # of the global config — preserves the "pure function" contract.
    gd = global_cfg.defaults
    base = {}
    for f in Defaults.__dataclass_fields__:
        val = getattr(gd, f)
        base[f] = list(val) if isinstance(val, list) else val
    if repo.defaults is not None:
        if not isinstance(repo.defaults, dict):
            raise ConfigError(
                f"Per-repo defaults must be a mapping, "
                f"got {type(repo.defaults).__name__} in {repo.path}"
            )
        valid_keys = set(Defaults.__dataclass_fields__)
        for k, v in repo.defaults.items():
            if k in valid_keys:
                base[k] = list(v) if isinstance(v, list) else v
            else:
                log.warning(
                    "Ignoring unrecognized per-repo defaults key %r in %s "
                    "(valid keys: %s)",
                    k, repo.path, ", ".join(sorted(valid_keys)),
                )
    # Top-level agents_dir / workflows_dir override defaults-level ones.
    # Track which path fields were explicitly overridden so we can rebase
    # them against repo.path — value equality is insufficient because a
    # repo may explicitly set the same value as the global default.
    _PATH_FIELDS = {"agents_dir", "workflows_dir"}
    _overridden_paths: set[str] = set()
    if repo.defaults is not None:
        for k in repo.defaults:
            if k in _PATH_FIELDS and k in base:
                _overridden_paths.add(k)
    if repo.agents_dir is not None:
        base["agents_dir"] = repo.agents_dir
        _overridden_paths.add("agents_dir")
    if repo.workflows_dir is not None:
        base["workflows_dir"] = repo.workflows_dir
        _overridden_paths.add("workflows_dir")

    # Rebase relative path overrides against repo.path so they resolve
    # correctly regardless of daemon CWD.
    for pf in _overridden_paths:
        raw_path = base[pf]
        if not isinstance(raw_path, str):
            raise ConfigError(
                f"Per-repo {pf} must be a path string, "
                f"got {type(raw_path).__name__} in {repo.path}"
            )
        p = Path(raw_path)
        if not p.is_absolute():
            base[pf] = str(Path(repo.path) / p)

    merged_defaults = Defaults(**base)

    return ResolvedRepoConfig(
        telegram=global_cfg.telegram,
        model_roles=merged_mr,
        defaults=merged_defaults,
        workflow_selection=merged_ws,
        version=global_cfg.version,
    )
