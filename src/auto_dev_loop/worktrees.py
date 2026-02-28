"""Git worktree management — one worktree per issue."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    pass


def create_worktree(repo_path: Path, worktree_path: Path, branch: str) -> None:
    """Create a git worktree with a new branch."""
    if worktree_path.exists():
        raise WorktreeError(f"Worktree path already exists: {worktree_path}")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree_path)],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"Failed to create worktree: {result.stderr.strip()}")


def delete_worktree(repo_path: Path, worktree_path: Path) -> None:
    """Remove a git worktree and prune."""
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        import shutil
        if worktree_path.exists():
            shutil.rmtree(worktree_path)
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo_path,
            capture_output=True,
        )


def list_worktrees(repo_path: Path) -> list[dict[str, str]]:
    """List all git worktrees for a repo."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    worktrees = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
        elif line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
    if current:
        worktrees.append(current)
    return worktrees
