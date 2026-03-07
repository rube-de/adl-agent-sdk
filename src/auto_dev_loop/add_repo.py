"""Repo onboarding logic for ``adl add``."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


class AddRepoError(Exception):
    pass


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


def check_gh_available() -> None:
    """Verify that the GitHub CLI is installed and authenticated."""
    try:
        subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise AddRepoError(
            "GitHub CLI (gh) is not installed or not working. "
            "Install from https://cli.github.com/"
        ) from exc

    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AddRepoError(
            "GitHub CLI is not authenticated. Run `gh auth login` first."
        )


def detect_github_remote(repo_path: Path) -> tuple[str, str]:
    """Detect GitHub owner/repo from a git repo directory.

    Uses ``gh repo view`` which reads the git remote and resolves it.
    Returns (owner, repo_name).
    """
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        raise AddRepoError(
            f"Could not detect GitHub remote: {result.stderr.strip()}\n"
            "Ensure the directory is a git repo with a GitHub remote."
        )
    name_with_owner = result.stdout.strip()
    parts = name_with_owner.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise AddRepoError(f"Unexpected nameWithOwner format: {name_with_owner!r}")
    return parts[0], parts[1]


def list_gh_projects(owner: str) -> list[dict[str, Any]]:
    """List GitHub Projects V2 for an owner."""
    result = subprocess.run(
        ["gh", "project", "list", "--owner", owner, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AddRepoError(
            f"Could not list projects for {owner}: {result.stderr.strip()}"
        )
    data = json.loads(result.stdout)
    return data.get("projects", [])


def list_status_options(owner: str, project_number: int) -> list[str]:
    """List Status field options for a GitHub Project V2.

    Returns a list of status option names (e.g. ["Todo", "In Progress", "Done"]).
    Returns empty list if no Status field is found.
    """
    result = subprocess.run(
        [
            "gh",
            "project",
            "field-list",
            str(project_number),
            "--owner",
            owner,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AddRepoError(f"Could not list project fields: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    for field in data.get("fields", []):
        if (
            field.get("name") == "Status"
            and field.get("type") == "ProjectV2SingleSelectField"
        ):
            return [opt["name"] for opt in field.get("options", [])]
    return []
