"""Repo onboarding logic for ``adl add``."""

from __future__ import annotations

import json
import subprocess  # nosec B404 — subprocess used with hardcoded args only
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import typer
import yaml
from ruamel.yaml import YAML

from ._paths import ADL_CONFIG
from .bundled import BUNDLED_AGENTS_DIR, BUNDLED_WORKFLOWS_DIR


class AddRepoError(Exception):
    pass


def _roundtrip_yaml() -> YAML:
    """Create a round-trip YAML instance that preserves comments and formatting."""
    rt = YAML()
    rt.preserve_quotes = True
    return rt


def scaffold_files(source_dir: Path | Traversable, target_dir: Path) -> list[str]:
    """Copy files from source_dir into target_dir, skipping existing.

    Uses read_bytes/write_bytes for Traversable compatibility (zip archives, wheels).
    Returns list of filenames that were actually copied.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src_file in sorted(source_dir.iterdir(), key=lambda p: p.name):
        if not src_file.is_file():
            continue
        dest = target_dir / src_file.name
        if dest.exists():
            continue
        dest.write_bytes(src_file.read_bytes())
        copied.append(src_file.name)
    return copied


def load_config_raw(config_path: Path) -> dict[str, Any]:
    """Load config YAML as a raw dict (no dataclass parsing).

    Raises AddRepoError if the file is not valid YAML or not a mapping.
    Raises FileNotFoundError if config_path does not exist.
    """
    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise AddRepoError(f"Config file {config_path} is not valid YAML.") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AddRepoError(
            f"Config file {config_path} must contain a YAML mapping at the top level."
        )
    return raw


def is_repo_configured(config_path: Path, repo_path: Path) -> bool:
    """Check if a repo path is already in the config."""
    data = load_config_raw(config_path)
    target = str(repo_path.resolve())
    for entry in data.get("repos", []):
        if not isinstance(entry, dict):
            continue
        existing = entry.get("path")
        if not isinstance(existing, str) or not existing:
            continue
        if str(Path(existing).expanduser().resolve()) == target:
            return True
    return False


def _remove_repo_config(config_path: Path, repo_path: Path) -> None:
    """Remove an existing repo entry from config by resolved path."""
    rt = _roundtrip_yaml()
    data = rt.load(config_path)
    if data is None:
        data = {}
    target = str(repo_path.resolve())
    repos = data.get("repos") or []
    if not isinstance(repos, list):
        raise AddRepoError(
            f"Config file {config_path} is invalid: 'repos' must be a list."
        )
    data["repos"] = [
        r for r in repos
        if not (
            isinstance(r, dict)
            and r.get("path")
            and str(Path(r["path"]).expanduser().resolve()) == target
        )
    ]
    rt.dump(data, config_path)


def append_repo_config(config_path: Path, entry: dict[str, Any]) -> None:
    """Append a repo entry to the config's repos list and write back."""
    rt = _roundtrip_yaml()
    data = rt.load(config_path)
    if data is None:
        data = {}
    repos = data.get("repos") or []
    if not isinstance(repos, list):
        raise AddRepoError(
            f"Config file {config_path} is invalid: 'repos' must be a list."
        )
    repos.append(entry)
    data["repos"] = repos
    rt.dump(data, config_path)


def check_gh_available() -> None:
    """Verify that the GitHub CLI is installed and authenticated."""
    try:
        subprocess.run(  # nosec B603 B607
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise AddRepoError("GitHub CLI timed out. Check your network connection.") from exc
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise AddRepoError(
            "GitHub CLI (gh) is not installed or not working. "
            "Install from https://cli.github.com/"
        ) from exc

    try:
        result = subprocess.run(  # nosec B603 B607
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise AddRepoError("GitHub CLI timed out checking auth status.") from exc
    except FileNotFoundError as exc:
        raise AddRepoError(
            "GitHub CLI (gh) is not available. "
            "Install from https://cli.github.com/"
        ) from exc
    if result.returncode != 0:
        raise AddRepoError(
            "GitHub CLI is not authenticated. Run `gh auth login` first."
        )


def detect_github_remote(repo_path: Path) -> tuple[str, str]:
    """Detect GitHub owner/repo from a git repo directory.

    Uses ``gh repo view`` which reads the git remote and resolves it.
    Returns (owner, repo_name).
    """
    try:
        result = subprocess.run(  # nosec B603 B607
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise AddRepoError("GitHub CLI timed out detecting remote. Check your network.") from exc
    if result.returncode != 0:
        raise AddRepoError(
            f"Could not detect GitHub remote: {result.stderr.strip()}\n"
            "Ensure the directory is a git repo with a GitHub remote."
        )
    name_with_owner = result.stdout.strip()
    owner, sep, repo_name = name_with_owner.partition("/")
    if not sep or not owner or not repo_name:
        raise AddRepoError(f"Unexpected nameWithOwner format: {name_with_owner!r}")
    return owner, repo_name


def list_gh_projects(owner: str) -> list[dict[str, Any]]:
    """List GitHub Projects V2 for an owner."""
    try:
        result = subprocess.run(  # nosec B603 B607
            ["gh", "project", "list", "--owner", owner, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise AddRepoError(f"GitHub CLI timed out listing projects for {owner}.") from exc
    if result.returncode != 0:
        raise AddRepoError(
            f"Could not list projects for {owner}: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AddRepoError(
            f"Could not parse project list response: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise AddRepoError("Unexpected project list response format (expected JSON object).")
    return data.get("projects", [])


def list_status_options(owner: str, project_number: int) -> list[str]:
    """List Status field options for a GitHub Project V2.

    Returns a list of status option names (e.g. ["Todo", "In Progress", "Done"]).
    Returns empty list if no Status field is found.
    """
    try:
        result = subprocess.run(  # nosec B603 B607
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
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise AddRepoError("GitHub CLI timed out listing project fields.") from exc
    if result.returncode != 0:
        raise AddRepoError(f"Could not list project fields: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AddRepoError(
            f"Could not parse project fields response: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise AddRepoError("Unexpected project fields response format (expected JSON object).")
    for field in data.get("fields", []):
        if (
            field.get("name") == "Status"
            and field.get("type") == "ProjectV2SingleSelectField"
        ):
            return [opt["name"] for opt in field.get("options", []) if opt.get("name")]
    return []


_COLUMN_CANDIDATES: dict[str, list[str]] = {
    "source": ["Ready for Dev", "Ready", "Todo", "To Do", "Backlog"],
    "in_progress": ["In Progress", "In progress", "Doing", "Active", "Working"],
    "done": ["Done", "Complete", "Completed", "Closed", "Shipped"],
}


def detect_column_defaults(options: list[str]) -> dict[str, str]:
    """Try to auto-match status options to source/in_progress/done columns.

    Returns a dict with matched keys only (may be partial or empty).
    Values are the actual option names (preserving original casing).
    """
    lower_to_actual = {opt.lower(): opt for opt in options}
    matched: dict[str, str] = {}
    for role, candidates in _COLUMN_CANDIDATES.items():
        for candidate in candidates:
            actual = lower_to_actual.get(candidate.lower())
            if actual is not None:
                matched[role] = actual
                break
    return matched


def _prompt_column(role: str, options: list[str], default: str | None) -> str:
    """Prompt user to select a column for a given role."""
    typer.echo(f"  Select column for '{role}':")
    for i, opt in enumerate(options, 1):
        marker = " (detected)" if opt == default else ""
        typer.echo(f"    {i}. {opt}{marker}")
    prompt_text = f"  {role} column number"
    if default and default in options:
        default_idx = options.index(default) + 1
        idx = typer.prompt(prompt_text, type=int, default=default_idx)
    else:
        idx = typer.prompt(prompt_text, type=int)
    if 1 <= idx <= len(options):
        return options[idx - 1]
    typer.echo(f"  Invalid selection '{idx}'. Enter a custom column name:")
    return typer.prompt(f"  Custom column name for {role}")


def _prompt_columns(options: list[str]) -> dict[str, str]:
    """Prompt user to map source/in_progress/done columns."""
    defaults = detect_column_defaults(options)

    if len(defaults) == 3:
        typer.echo("Detected column mappings:")
        for role, col in defaults.items():
            typer.echo(f"  {role}: {col}")
        if typer.confirm("Use these column mappings?", default=True):
            return defaults

    typer.echo("Map project columns:")
    max_attempts = 5
    for attempt in range(max_attempts):
        columns = {}
        for role in ("source", "in_progress", "done"):
            columns[role] = _prompt_column(role, options, defaults.get(role))
        if len(set(columns.values())) == 3:
            return columns
        if len(set(options)) < 3:
            typer.echo(
                f"  Only {len(set(options))} distinct options available — "
                "cannot map 3 unique columns. Use custom column names.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo("  Each role must map to a different column. Please try again.")
    typer.echo("  Too many invalid attempts.", err=True)
    raise typer.Exit(1)


def _prompt_project(projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Prompt user to select a project from the list."""
    if len(projects) == 1:
        project = projects[0]
        typer.echo(f"Using project #{project.get('number', '?')} - {project.get('title', '(untitled)')}")
        return project

    typer.echo("Available projects:")
    for i, p in enumerate(projects, 1):
        typer.echo(f"  {i}. #{p.get('number', '?')} - {p.get('title', '(untitled)')}")
    idx = typer.prompt("Select project (menu index or project number)", type=int)
    # Try as 1-based menu index first
    if 1 <= idx <= len(projects):
        return projects[idx - 1]
    # Try as a project number
    for p in projects:
        if p.get("number") == idx:
            return p
    raise AddRepoError(f"Invalid selection: {idx}")


def run_add_wizard(
    repo_path: Path | None = None,
    config_path: Path | None = None,
) -> None:
    """Run the interactive repo onboarding wizard."""
    config_path = config_path or ADL_CONFIG

    # 1. Check config exists
    if not config_path.exists():
        typer.echo("No config found. Run `adl init` first.", err=True)
        raise typer.Exit(1)

    # 2. Resolve repo path (supports worktrees, submodules, and subdirs)
    candidate = (repo_path or Path.cwd()).resolve()
    if not candidate.exists():
        typer.echo(f"{candidate} does not exist.", err=True)
        raise typer.Exit(1)
    if (candidate / ".git").exists():
        # .git dir (normal repo) or .git file (worktree/submodule)
        resolved = candidate
    else:
        # May be a subdirectory — try git rev-parse to find repo root
        try:
            result = subprocess.run(  # nosec B603 B607
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                cwd=candidate,
                timeout=10,
            )
            if result.returncode == 0:
                resolved = Path(result.stdout.strip()).resolve()
            else:
                typer.echo(f"{candidate} is not a git repository.", err=True)
                raise typer.Exit(1)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            typer.echo(f"{candidate} is not a git repository.", err=True)
            raise typer.Exit(1)

    # 3. Check for duplicates
    try:
        already_configured = is_repo_configured(config_path, resolved)
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if already_configured:
        typer.echo(f"Repository {resolved} is already configured.")
        if not typer.confirm("Reconfigure?", default=False):
            raise typer.Exit(0)
        try:
            _remove_repo_config(config_path, resolved)
        except AddRepoError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    # 4. Check gh CLI
    try:
        check_gh_available()
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # 5. Detect GitHub info
    try:
        owner, repo = detect_github_remote(resolved)
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Detected GitHub repo: {owner}/{repo}")

    # 6. List and select project
    try:
        projects = list_gh_projects(owner)
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if not projects:
        typer.echo(
            f"No GitHub Projects V2 found for {owner}.\n"
            f"Create one at https://github.com/orgs/{owner}/projects "
            f"or https://github.com/users/{owner}/projects",
            err=True,
        )
        raise typer.Exit(1)

    try:
        project = _prompt_project(projects)
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # 7. Map columns
    try:
        status_options = list_status_options(owner, project["number"])
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if status_options:
        columns = _prompt_columns(status_options)
    else:
        typer.echo("No Status field found.")
        typer.echo("Enter the names of the three status columns in your project.")
        max_attempts = 5
        for _attempt in range(max_attempts):
            columns = {
                "source": typer.prompt(
                    "Column name for items ready for development",
                    default="Ready for Dev",
                ),
                "in_progress": typer.prompt(
                    "Column name for items in progress",
                    default="In Progress",
                ),
                "done": typer.prompt(
                    "Column name for completed items",
                    default="Done",
                ),
            }
            if len(set(columns.values())) == 3:
                break
            typer.echo("  Each role must map to a different column. Please try again.")
        else:
            typer.echo("  Too many invalid attempts.", err=True)
            raise typer.Exit(1)

    # 8. Scaffold agents and workflows
    agents_copied = scaffold_files(BUNDLED_AGENTS_DIR, resolved / "agents")
    workflows_copied = scaffold_files(BUNDLED_WORKFLOWS_DIR, resolved / "workflows")
    if agents_copied:
        typer.echo(f"Scaffolded agents: {', '.join(agents_copied)}")
    if workflows_copied:
        typer.echo(f"Scaffolded workflows: {', '.join(workflows_copied)}")
    if not agents_copied and not workflows_copied:
        typer.echo("All agent/workflow files already exist, nothing scaffolded.")

    # 9. Append to config
    entry = {
        "path": str(resolved),
        "project_number": project["number"],
        "owner": owner,
        "repo": repo,
        "columns": columns,
    }
    try:
        append_repo_config(config_path, entry)
    except AddRepoError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Added {owner}/{repo}. Run `adl run` to start processing.")
