"""Asyncio daemon — polls GitHub Projects, processes issues concurrently."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_config
from .models import Config, Issue, RepoConfig
from .orchestrator import process_issue
from .poller import poll_project_issues

log = logging.getLogger(__name__)


@dataclass
class DaemonState:
    active_issues: set[str] = field(default_factory=set)
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)


def issue_key(issue: Issue) -> str:
    return f"{issue.repo}#{issue.number}"


def should_process_issue(
    issue: Issue,
    state: DaemonState,
    max_concurrent: int = 1,
) -> bool:
    """Check if this issue should be picked up."""
    key = issue_key(issue)
    if key in state.active_issues:
        return False
    if len(state.active_issues) >= max_concurrent:
        return False
    return True


def _get_repo_owner(repo_cfg: RepoConfig) -> str:
    """Extract project owner from repo path (e.g., 'owner/repo' -> 'owner')."""
    return repo_cfg.path.split("/")[0] if "/" in repo_cfg.path else repo_cfg.path


async def run_poll_cycle(config: Config, state: DaemonState) -> None:
    """Run one poll cycle across all configured repos."""
    max_concurrent = config.defaults.max_concurrent

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
            log.exception(f"Failed to poll {repo_cfg.path}")
            continue

        for issue in issues:
            if not should_process_issue(issue, state, max_concurrent):
                continue

            key = issue_key(issue)
            state.active_issues.add(key)
            log.info(f"Starting processing of {key}")

            try:
                result = await process_issue(
                    issue, config, repo_path=Path(repo_cfg.path),
                )
                log.info(f"Completed {key}: {result.state}")
            except Exception:
                log.exception(f"Failed processing {key}")
            finally:
                state.active_issues.discard(key)


async def daemon_loop(config: Config) -> None:
    """Main daemon loop — poll, process, sleep, repeat."""
    state = DaemonState()
    poll_interval = config.defaults.poll_interval
    log.info(f"ADL daemon starting (poll_interval={poll_interval}s)")

    while True:
        try:
            await run_poll_cycle(config, state)
        except Exception:
            log.exception("Poll cycle failed")

        await asyncio.sleep(poll_interval)


def run_daemon(config_path: str | None = None) -> None:
    """Entry point for the daemon."""
    path = Path(config_path) if config_path else Path.home() / ".claude" / "auto-dev.yaml"
    config = load_config(path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(daemon_loop(config))
