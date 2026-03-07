"""Repo onboarding logic for ``adl add``."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


def scaffold_files(source_dir: Path, target_dir: Path) -> list[str]:
    """Copy files from source_dir into target_dir, skipping existing.

    Returns list of filenames that were actually copied.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src_file in sorted(source_dir.iterdir()):
        if not src_file.is_file():
            continue
        dest = target_dir / src_file.name
        if dest.exists():
            continue
        shutil.copy2(src_file, dest)
        copied.append(src_file.name)
    return copied


def load_config_raw(config_path: Path) -> dict[str, Any]:
    """Load config YAML as a raw dict (no dataclass parsing)."""
    return yaml.safe_load(config_path.read_text()) or {}


def is_repo_configured(config_path: Path, repo_path: Path) -> bool:
    """Check if a repo path is already in the config."""
    data = load_config_raw(config_path)
    target = str(repo_path.resolve())
    for entry in data.get("repos", []):
        if not isinstance(entry, dict):
            continue
        existing = entry.get("path", "")
        if str(Path(existing).expanduser().resolve()) == target:
            return True
    return False


def append_repo_config(config_path: Path, entry: dict[str, Any]) -> None:
    """Append a repo entry to the config's repos list and write back."""
    data = load_config_raw(config_path)
    repos = data.get("repos", [])
    repos.append(entry)
    data["repos"] = repos
    config_path.write_text(yaml.safe_dump(data, sort_keys=False))
