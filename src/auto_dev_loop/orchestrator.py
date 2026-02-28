"""Issue lifecycle orchestrator — state machine driving claim->plan->dev->PR->review."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .dev_loop import dev_loop, MaxDevCyclesError
from .models import Config, Issue
from .plan_loop import plan_loop, MaxPlanIterationsError
from .review_loop import review_loop, MaxReviewCyclesError
from .workflow_router import select_workflow
from .worktrees import create_worktree, delete_worktree

log = logging.getLogger(__name__)


class IssueState(str, Enum):
    CLAIMED = "claimed"
    PLANNING = "planning"
    DEVELOPING = "developing"
    PR_CREATED = "pr_created"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


@dataclass
class ProcessResult:
    state: IssueState
    pr_number: int | None = None
    error: str | None = None


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
    branch = f"adl/{issue.number}-{issue.title[:30].replace(' ', '-').lower()}"

    proc = await asyncio.create_subprocess_exec(
        "git", "push", "-u", "origin", branch,
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

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


async def process_issue(
    issue: Issue,
    config: Config,
    repo_path: Path | None = None,
) -> ProcessResult:
    """Drive a single issue through the full lifecycle."""
    _repo_path = repo_path or Path(".")
    branch = f"adl/{issue.number}-{issue.title[:30].replace(' ', '-').lower()}"
    worktree_path = _repo_path / ".worktrees" / branch

    try:
        create_worktree(_repo_path, worktree_path, branch)
        log.info(f"Processing {issue.repo}#{issue.number} in {worktree_path}")

        workflow_id = select_workflow(issue, config.workflow_selection)
        log.info(f"Selected workflow: {workflow_id}")

        plan_result = await plan_loop(issue, worktree_path, config)
        log.info(f"Plan approved after {plan_result.iterations} iterations")

        dev_result = await dev_loop(issue, plan_result.plan, worktree_path, config)
        log.info(f"Dev completed after {dev_result.cycles} cycles")

        pr_number = await create_pr(issue, worktree_path)
        log.info(f"PR #{pr_number} created")

        review_result = await review_loop(issue, pr_number, worktree_path, config)
        log.info(f"Review completed after {review_result.cycles} cycles")

        return ProcessResult(state=IssueState.COMPLETED, pr_number=pr_number)

    except (MaxPlanIterationsError, MaxDevCyclesError, MaxReviewCyclesError) as e:
        log.error(f"Loop exhausted for {issue.repo}#{issue.number}: {e}")
        return ProcessResult(state=IssueState.FAILED, error=str(e))

    except Exception as e:
        log.exception(f"Unexpected error processing {issue.repo}#{issue.number}")
        return ProcessResult(state=IssueState.FAILED, error=str(e))

    finally:
        try:
            delete_worktree(_repo_path, worktree_path)
        except Exception:
            log.warning(f"Failed to clean up worktree {worktree_path}")
