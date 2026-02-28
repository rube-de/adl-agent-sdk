"""Asyncio daemon — polls GitHub Projects, processes issues concurrently."""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_config
from .issue_logging import IssueLogger
from .models import Config, Issue, RepoConfig
from .orchestrator import IssueState, process_issue
from .poller import poll_project_issues
from .state import StateStore

log = logging.getLogger(__name__)

ADL_HOME = Path.home() / ".adl"
ADL_CONFIG = ADL_HOME / "config.yaml"
ADL_STATE_DB = ADL_HOME / "state.db"
ADL_LOGS_DIR = ADL_HOME / "logs"


@dataclass
class DaemonState:
    active_issues: set[str] = field(default_factory=set)
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    completed_keys: set[str] = field(default_factory=set)
    store: StateStore | None = None


def issue_key(issue: Issue) -> str:
    return f"{issue.repo}#{issue.number}"


def should_process_issue(
    issue: Issue,
    state: DaemonState,
    max_concurrent: int = 1,
) -> bool:
    """Check if this issue should be picked up."""
    key = issue_key(issue)
    if key in state.completed_keys:
        return False
    if key in state.active_issues:
        return False
    if len(state.active_issues) >= max_concurrent:
        return False
    return True


def _get_repo_owner(repo_cfg: RepoConfig) -> str:
    """Extract project owner from repo path (e.g., 'owner/repo' -> 'owner')."""
    return repo_cfg.path.split("/")[0] if "/" in repo_cfg.path else repo_cfg.path


def _on_issue_done(key: str, state: DaemonState, _task: asyncio.Task) -> None:
    """Callback: clean up state when an issue task finishes."""
    state.active_issues.discard(key)
    state.tasks.pop(key, None)


def _make_issue_logger(issue: Issue) -> IssueLogger:
    """Create a per-issue logger under ~/.adl/logs/."""
    repo_slug = issue.repo.replace("/", "-")
    return IssueLogger(ADL_LOGS_DIR, repo_slug, issue.number)


async def _process_issue_task(
    issue: Issue, config: Config, repo_path: Path, key: str,
    state: DaemonState,
) -> None:
    """Task wrapper — runs process_issue and logs the outcome."""
    store = state.store
    logger = _make_issue_logger(issue)

    try:
        if store:
            await store.upsert_issue(
                issue.repo, issue.number, issue.title, IssueState.CLAIMED.value,
                project_item_id=issue.project_item_id,
            )

        result = await process_issue(
            issue, config, repo_path=repo_path, store=store, issue_logger=logger,
        )
        log.info("Completed %s: %s", key, result.state)

        if store:
            await store.update_state(issue.id, result.state.value)

        if result.state in (IssueState.COMPLETED, IssueState.FAILED, IssueState.ESCALATED):
            state.completed_keys.add(key)

    except Exception:
        log.exception("Failed processing %s", key)
        if store:
            try:
                await store.update_state(issue.id, IssueState.FAILED.value)
            except Exception:
                log.warning("Could not persist failure state for %s", key)
        state.completed_keys.add(key)


async def run_poll_cycle(
    config: Config, state: DaemonState, *, once: bool = False,
) -> None:
    """Run one poll cycle across all configured repos.

    When *once* is True, process at most one issue inline and return.
    Otherwise, spawn concurrent tasks gated by max_concurrent.
    """
    max_concurrent = 1 if once else config.defaults.max_concurrent

    for repo_cfg in config.repos:
        if not isinstance(repo_cfg, RepoConfig):
            continue

        owner = _get_repo_owner(repo_cfg)
        source_column = repo_cfg.columns.get("source", "Ready for Dev")

        try:
            issues = await poll_project_issues(
                owner=owner,
                project_number=repo_cfg.project_number,
                target_column=source_column,
            )
        except Exception:
            log.exception("Failed to poll %s", repo_cfg.path)
            continue

        for issue in issues:
            if not should_process_issue(issue, state, max_concurrent):
                continue

            key = issue_key(issue)
            state.active_issues.add(key)
            log.info("Starting processing of %s", key)

            if once:
                # --once: process inline, single issue, then return
                store = state.store
                logger = _make_issue_logger(issue)
                try:
                    if store:
                        await store.upsert_issue(
                            issue.repo, issue.number, issue.title,
                            IssueState.CLAIMED.value,
                            project_item_id=issue.project_item_id,
                        )
                    result = await process_issue(
                        issue, config, repo_path=Path(repo_cfg.path),
                        store=store, issue_logger=logger,
                    )
                    log.info("Completed %s: %s", key, result.state)
                    if store:
                        await store.update_state(issue.id, result.state.value)
                except Exception:
                    log.exception("Failed processing %s", key)
                finally:
                    state.active_issues.discard(key)
                return

            # Normal mode: spawn a concurrent task
            task = asyncio.create_task(
                _process_issue_task(issue, config, Path(repo_cfg.path), key, state),
                name=f"adl:{key}",
            )
            task.add_done_callback(
                functools.partial(_on_issue_done, key, state),
            )
            state.tasks[key] = task


async def drain_tasks(state: DaemonState) -> None:
    """Await all in-flight issue tasks. Used on shutdown / --once exit."""
    if not state.tasks:
        return
    log.info("Draining %d in-flight task(s)…", len(state.tasks))
    await asyncio.gather(*state.tasks.values(), return_exceptions=True)


async def daemon_loop(config: Config, *, once: bool = False) -> None:
    """Main daemon loop — poll, process, sleep, repeat.

    If *once* is True, run a single poll cycle (processing at most one issue)
    and return.  Useful for CI / integration testing.
    """
    state = DaemonState()
    poll_interval = config.defaults.poll_interval

    # Persistent state
    ADL_HOME.mkdir(parents=True, exist_ok=True)
    store = StateStore(ADL_STATE_DB)
    await store.init()
    state.store = store

    # Load previously-completed issues to avoid reprocessing
    state.completed_keys = await store.list_terminal_issue_keys()
    if state.completed_keys:
        log.info("Loaded %d completed issue(s) from state DB", len(state.completed_keys))

    log.info(
        "ADL daemon starting (poll_interval=%ds, once=%s)",
        poll_interval, once,
    )

    try:
        while True:
            try:
                await run_poll_cycle(config, state, once=once)
            except Exception:
                log.exception("Poll cycle failed")

            if once:
                log.info("--once: exiting after single cycle")
                return

            await asyncio.sleep(poll_interval)
    finally:
        await drain_tasks(state)
        await store.close()


def run_daemon(config_path: str | None = None, *, once: bool = False) -> None:
    """Entry point for the daemon."""
    path = Path(config_path) if config_path else ADL_CONFIG
    config = load_config(path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(daemon_loop(config, once=once))
