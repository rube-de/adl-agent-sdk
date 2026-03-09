"""Issue lifecycle orchestrator — state machine driving claim->plan->dev->PR->review."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .agent_loader import load_agents
from .branch import build_branch_name
from .dispatcher import OrchestratorDispatcher
from .hooks import CommandGuard, LoggingSecurityHandler, create_default_guard
from .models import AppConfig, Issue
from .pr import build_pr_command, create_pr  # noqa: F401 — re-exported for tests
from .workflow_engine import execute_workflow
from .workflow_loader import load_all_workflows
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


_RESULT_STATE_MAP = {
    "completed": IssueState.COMPLETED,
    "escalated": IssueState.ESCALATED,
    "vetoed": IssueState.ESCALATED,
}


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
    config: AppConfig,
    repo_path: Path | None = None,
    telegram: TelegramBot | None = None,
    store: StateStore | None = None,
    issue_logger: IssueLogger | None = None,
) -> ProcessResult:
    """Drive a single issue through the full lifecycle."""
    _repo_path = repo_path or Path(".")
    branch = build_branch_name(issue)
    worktree_path = _repo_path / ".worktrees" / branch

    guard = create_default_guard(handler=LoggingSecurityHandler())

    try:
        await create_worktree(_repo_path, worktree_path, branch)
        log.info(f"Processing {issue.repo}#{issue.number} in {worktree_path}")

        # Select and load workflow
        workflow_id = select_workflow(issue, config.workflow_selection)
        log.info(f"Selected workflow: {workflow_id}")
        if issue_logger:
            issue_logger.log_event("workflow_selected", {"workflow": workflow_id})

        workflows = load_all_workflows(Path(config.defaults.workflows_dir))
        if workflow_id not in workflows:
            raise ValueError(f"Workflow '{workflow_id}' not found in {config.defaults.workflows_dir}")
        workflow = workflows[workflow_id]

        agents = load_agents(Path(config.defaults.agents_dir))

        # Create dispatcher with all dependencies
        dispatcher = OrchestratorDispatcher(
            agents=agents,
            config=config,
            worktree=worktree_path,
            guard=guard,
            telegram=telegram,
            issue=issue,
        )

        # Execute workflow — replaces hardcoded plan→dev→PR→review pipeline
        await _transition(IssueState.PLANNING, store, issue_logger, issue)
        result = await execute_workflow(workflow, issue, dispatcher)
        await _flush_security_events(guard, issue, telegram)

        # Map workflow result to process result
        final_state = _RESULT_STATE_MAP.get(result.status, IssueState.FAILED)
        await _transition(final_state, store, issue_logger, issue)

        return ProcessResult(
            state=final_state,
            pr_number=dispatcher.pr_number,
            error=f"Workflow {result.status} at stage {result.stage}" if result.status != "completed" else None,
        )

    except Exception as e:
        log.exception(f"Unexpected error processing {issue.repo}#{issue.number}")
        await _transition(IssueState.FAILED, store, issue_logger, issue)
        return ProcessResult(state=IssueState.FAILED, error=str(e))

    finally:
        # Flush any remaining events from the failed/interrupted phase
        await _flush_security_events(guard, issue, telegram)
        try:
            await delete_worktree(_repo_path, worktree_path)
        except Exception:
            log.warning(f"Failed to clean up worktree {worktree_path}")
