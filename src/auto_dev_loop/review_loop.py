"""PR review loop — fetches review comments, applies fixes, pushes, waits."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .agent_loader import load_agents
from .agent_query import agent_query
from .comments import fetch_pr_comments, parse_review_comments, filter_actionable, format_for_agent
from .hooks import CommandGuard
from .models import Config, Issue
from .pr_status import check_pr_status

log = logging.getLogger(__name__)


class MaxReviewCyclesError(Exception):
    pass


class PushFixesError(Exception):
    pass


@dataclass
class ReviewLoopResult:
    cycles: int
    merged: bool


async def push_fixes(worktree: Path, issue: Issue) -> bool:
    """Stage, commit, and push fixes. Returns True if changes were pushed."""
    proc = await asyncio.create_subprocess_exec(
        "git", "add", "-u",
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise PushFixesError(f"git add failed: {stderr.decode().strip()}")

    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", f"fix: address review comments for #{issue.number}",
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Nothing to commit is not an error — agent may have found no changes needed
        log.info("Nothing to commit, skipping push")
        return False

    proc = await asyncio.create_subprocess_exec(
        "git", "push",
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise PushFixesError(f"git push failed: {stderr.decode().strip()}")

    return True


async def review_loop(
    issue: Issue,
    pr_number: int,
    worktree: Path,
    config: Config,
    guard: CommandGuard | None = None,
) -> ReviewLoopResult:
    """Iterate on PR review comments until approved or max cycles."""
    backoff = config.defaults.review_backoff
    max_cycles = config.defaults.max_review_cycles

    # Check initial status
    status = await check_pr_status(issue.repo, pr_number)
    if status.ready_to_merge:
        return ReviewLoopResult(cycles=0, merged=False)

    agents = load_agents(Path(config.defaults.agents_dir))

    for cycle in range(1, max_cycles + 1):
        log.info(f"Review cycle {cycle}/{max_cycles}")

        # Fetch and format comments
        raw_comments = await fetch_pr_comments(issue.repo, pr_number)
        parsed = parse_review_comments(raw_comments)
        actionable = filter_actionable(parsed)

        if not actionable:
            log.info("No actionable comments, waiting...")
        else:
            comment_text = format_for_agent(actionable)
            await agent_query(
                agent_def=agents["pr_fixer"],
                prompt=f"Fix these PR review comments:\n\n{comment_text}",
                worktree=worktree,
                config=config,
                guard=guard,
            )
            await push_fixes(worktree, issue)

        # Wait with backoff
        wait_time = backoff[min(cycle - 1, len(backoff) - 1)]
        log.info(f"Waiting {wait_time}s before checking PR status...")
        await asyncio.sleep(wait_time)

        # Check status again
        status = await check_pr_status(issue.repo, pr_number)
        if status.ready_to_merge:
            return ReviewLoopResult(cycles=cycle, merged=False)

    raise MaxReviewCyclesError(
        f"PR not approved after {max_cycles} review cycles"
    )
