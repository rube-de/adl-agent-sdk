"""Shared path constants for Auto Dev Loop."""

from pathlib import Path

ADL_HOME = Path.home() / ".adl"
ADL_CONFIG = ADL_HOME / "config.yaml"


def repo_slug(owner: str, repo: str) -> str:
    """Produce a collision-free slug: ``<owner>/<repo>``.

    Uses ``/`` as separator so that owner and repo become separate path
    segments, avoiding ambiguity when either component contains ``-``.
    """
    clean_owner = owner.strip().replace("/", "-")
    clean_repo = repo.strip().replace("/", "-")
    return f"{clean_owner}/{clean_repo}"


def repo_state_dir(slug: str) -> Path:
    """Return ``~/.adl/repos/<slug>/`` for a given repo slug."""
    return ADL_HOME / "repos" / slug
