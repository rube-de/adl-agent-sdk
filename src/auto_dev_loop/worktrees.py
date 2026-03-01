"""Git worktree management — one worktree per issue."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class WorktreeError(Exception):
    pass


async def create_worktree(repo_path: Path, worktree_path: Path, branch: str) -> None:
    """Create a git worktree with a new branch."""
    # Defense-in-depth: reject paths with traversal components
    if ".." in worktree_path.parts:
        raise WorktreeError(
            f"Worktree path contains '..' traversal component: {worktree_path}"
        )

    if worktree_path.exists():
        raise WorktreeError(f"Worktree path already exists: {worktree_path}")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch, str(worktree_path),
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise WorktreeError(f"Failed to create worktree: {stderr.decode().strip()}")


async def delete_worktree(repo_path: Path, worktree_path: Path) -> None:
    """Remove a git worktree and prune."""
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", "--force", str(worktree_path),
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode != 0:
        if worktree_path.exists():
            shutil.rmtree(worktree_path)
        prune = await asyncio.create_subprocess_exec(
            "git", "worktree", "prune",
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await prune.communicate()


async def list_worktrees(repo_path: Path) -> list[dict[str, str]]:
    """List all git worktrees for a repo."""
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "list", "--porcelain",
        cwd=str(repo_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise WorktreeError(f"Failed to list worktrees: {stderr.decode().strip()}")

    worktrees = []
    current: dict[str, str] = {}
    for line in stdout.decode().splitlines():
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
