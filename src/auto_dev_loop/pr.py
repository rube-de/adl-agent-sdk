"""PR creation helpers — factored out to avoid circular imports."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .branch import build_branch_name
from .models import Issue


def build_pr_command(
    repo: str, title: str, body: str, branch: str,
) -> list[str]:
    """Build the gh pr create command."""
    return [
        "gh", "pr", "create",
        "--repo", repo,
        "--title", title,
        "--body", body,
        "--head", branch,
    ]


async def create_pr(issue: Issue, worktree: Path) -> int:
    """Create a PR via gh CLI, return PR number."""
    branch = build_branch_name(issue)

    proc = await asyncio.create_subprocess_exec(
        "git", "push", "-u", "origin", branch,
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git push failed: {stderr.decode().strip()}")

    cmd = build_pr_command(
        repo=issue.repo,
        title=f"[ADL] {issue.title}",
        body=f"Resolves #{issue.number}\n\nAutonomously implemented by ADL.",
        branch=branch,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {stderr.decode()}")

    url = stdout.decode().strip()
    pr_number = int(url.rstrip("/").split("/")[-1])
    return pr_number
