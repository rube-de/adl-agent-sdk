"""Issue lifecycle orchestrator — state machine driving claim->plan->dev->PR->review."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .dev_loop import dev_loop, MaxDevCyclesError
from .hooks import CommandGuard, LoggingSecurityHandler, SecurityEvent, create_default_guard
from .models import Config, Issue
from .plan_loop import plan_loop, MaxPlanIterationsError
from .review_loop import review_loop, MaxReviewCyclesError
from .workflow_router import select_workflow
from .worktrees import create_worktree, delete_worktree

if TYPE_CHECKING:
    from .issue_logging import IssueLogger
    from .state import StateStore
    from .telegram import TelegramBot

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


async def _flush_security_events(
    guard: CommandGuard,
    issue: Issue,
    telegram: TelegramBot | None,
) -> None:
    """Drain any blocked-command events and send to Telegram if available."""
    events = guard.drain_events()
    if not events:
        return
    log.warning(
        "%d command(s) blocked for %s#%d",
        len(events), issue.repo, issue.number,
    )
    if telegram is not None:
        await telegram.notify_security(
            issue=issue,
            blocked_commands=[
                {"command": e.command, "reason": e.reason} for e in events
            ],
        )


async def _transition(
    state: IssueState,
    store: StateStore | None,
    issue_logger: IssueLogger | None,
    issue: Issue,
) -> None:
    """Persist a state transition to both the DB and per-issue log."""
    if store:
        row = await store.get_issue(issue.repo, issue.number)
        if row:
            await store.update_state(row["id"], state.value)
    if issue_logger:
        issue_logger.write_state({"state": state.value, "issue": issue.number})
        issue_logger.log_event("state_transition", {"state": state.value})


async def process_issue(
    issue: Issue,
    config: Config,
    repo_path: Path | None = None,
    telegram: TelegramBot | None = None,
    store: StateStore | None = None,
    issue_logger: IssueLogger | None = None,
) -> ProcessResult:
    """Drive a single issue through the full lifecycle."""
    _repo_path = repo_path or Path(".")
    branch = f"adl/{issue.number}-{issue.title[:30].replace(' ', '-').lower()}"
    worktree_path = _repo_path / ".worktrees" / branch

    guard = create_default_guard(handler=LoggingSecurityHandler())

    try:
        create_worktree(_repo_path, worktree_path, branch)
        log.info(f"Processing {issue.repo}#{issue.number} in {worktree_path}")

        workflow_id = select_workflow(issue, config.workflow_selection)
        log.info(f"Selected workflow: {workflow_id}")
        if issue_logger:
            issue_logger.log_event("workflow_selected", {"workflow": workflow_id})

        # --- Planning ---
        await _transition(IssueState.PLANNING, store, issue_logger, issue)
        plan_result = await plan_loop(issue, worktree_path, config, guard=guard)
        await _flush_security_events(guard, issue, telegram)
        log.info(f"Plan approved after {plan_result.iterations} iterations")

        # --- Development ---
        await _transition(IssueState.DEVELOPING, store, issue_logger, issue)
        dev_result = await dev_loop(
            issue, plan_result.plan, worktree_path, config, guard=guard,
        )
        await _flush_security_events(guard, issue, telegram)
        log.info(f"Dev completed after {dev_result.cycles} cycles")

        # --- PR creation ---
        pr_number = await create_pr(issue, worktree_path)
        await _transition(IssueState.PR_CREATED, store, issue_logger, issue)
        log.info(f"PR #{pr_number} created")

        # --- Review ---
        await _transition(IssueState.IN_REVIEW, store, issue_logger, issue)
        review_result = await review_loop(
            issue, pr_number, worktree_path, config, guard=guard,
        )
        await _flush_security_events(guard, issue, telegram)
        log.info(f"Review completed after {review_result.cycles} cycles")

        await _transition(IssueState.COMPLETED, store, issue_logger, issue)
        return ProcessResult(state=IssueState.COMPLETED, pr_number=pr_number)

    except (MaxPlanIterationsError, MaxDevCyclesError, MaxReviewCyclesError) as e:
        log.error(f"Loop exhausted for {issue.repo}#{issue.number}: {e}")
        await _transition(IssueState.FAILED, store, issue_logger, issue)
        return ProcessResult(state=IssueState.FAILED, error=str(e))

    except Exception as e:
        log.exception(f"Unexpected error processing {issue.repo}#{issue.number}")
        await _transition(IssueState.FAILED, store, issue_logger, issue)
        return ProcessResult(state=IssueState.FAILED, error=str(e))

    finally:
        # Flush any remaining events from the failed/interrupted phase
        await _flush_security_events(guard, issue, telegram)
        try:
            delete_worktree(_repo_path, worktree_path)
        except Exception:
            log.warning(f"Failed to clean up worktree {worktree_path}")
