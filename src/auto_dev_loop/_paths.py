"""Shared path constants for Auto Dev Loop."""

from pathlib import Path

ADL_HOME = Path.home() / ".adl"
ADL_CONFIG = ADL_HOME / "config.yaml"
ADL_STATE_DB = ADL_HOME / "state.db"
ADL_LOGS_DIR = ADL_HOME / "logs"


def repo_slug(owner: str, repo: str) -> str:
    """Produce a filesystem-safe slug: ``<owner>-<repo>``."""
    return f"{owner.strip()}-{repo.strip()}".replace("/", "-")


def repo_state_dir(slug: str) -> Path:
    """Return ``~/.adl/repos/<slug>/`` for a given repo slug."""
    return ADL_HOME / "repos" / slug
