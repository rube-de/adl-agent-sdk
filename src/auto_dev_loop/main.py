"""Asyncio daemon — polls GitHub Projects, processes issues concurrently."""

from __future__ import annotations

import asyncio
import functools
import logging
import signal
from dataclasses import dataclass, field
from pathlib import Path

from ._paths import ADL_CONFIG, ADL_HOME, repo_slug, repo_state_dir
from .config import load_config, resolve_repo_config
from .issue_logging import IssueLogger
from .models import AppConfig, Config, Issue, RepoConfig
from .orchestrator import IssueState, process_issue
from .poller import poll_project_issues
from .state import StateStore

log = logging.getLogger(__name__)


@dataclass
class DaemonState:
    active_issues: set[str] = field(default_factory=set)
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    completed_keys: set[str] = field(default_factory=set)
    stores: dict[str, StateStore] = field(default_factory=dict)
    shutdown_event: asyncio.Event | None = None


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
    """Get GitHub owner from repo config.

    Prefers the explicit ``owner`` field. Falls back to splitting ``path``
    on '/' for backward compatibility with 'owner/repo'-style paths.

    Raises :class:`ValueError` when an owner cannot be derived (e.g.
    absolute paths without an explicit ``owner`` field).
    """
    if repo_cfg.owner:
        return repo_cfg.owner
    candidate = repo_cfg.path.split("/")[0] if "/" in repo_cfg.path else repo_cfg.path
    if not candidate:
        raise ValueError(
            f"Cannot derive owner for repo path '{repo_cfg.path}'. "
            "Set the explicit 'owner' field in your repo config."
        )
    return candidate


def _get_repo_name(repo_cfg: RepoConfig) -> str:
    """Extract the repository name from a RepoConfig.

    Prefers the explicit ``repo`` field (set by ``adl add``), falling back
    to the last segment of the path for backwards compatibility.
    """
    if repo_cfg.repo:
        return repo_cfg.repo
    return Path(repo_cfg.path.rstrip("/\\")).name


async def _get_or_create_store(
    state: DaemonState,
    slug: str,
) -> StateStore:
    """Return an existing per-repo StateStore or create + init one."""
    if slug in state.stores:
        return state.stores[slug]
    state_dir = repo_state_dir(slug)
    state_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(state_dir / "state.db")
    await store.init()
    state.stores[slug] = store
    return store


def _on_issue_done(key: str, state: DaemonState, _task: asyncio.Task) -> None:
    """Callback: clean up state when an issue task finishes."""
    state.active_issues.discard(key)
    state.tasks.pop(key, None)


def _make_issue_logger(issue: Issue, logs_dir: Path) -> IssueLogger:
    """Create a per-issue logger under the repo-specific logs directory."""
    return IssueLogger(logs_dir, issue.number)


async def _process_issue_task(
    issue: Issue,
    config: AppConfig,
    repo_path: Path,
    key: str,
    state: DaemonState,
    slug: str,
    store: StateStore,
) -> None:
    """Task wrapper — runs process_issue and logs the outcome."""
    logs_dir = repo_state_dir(slug) / "logs"
    logger = _make_issue_logger(issue, logs_dir)

    try:
        await store.upsert_issue(
            issue.repo,
            issue.number,
            issue.title,
            IssueState.CLAIMED.value,
            project_item_id=issue.project_item_id,
        )

        result = await process_issue(
            issue,
            config,
            repo_path=repo_path,
            store=store,
            issue_logger=logger,
        )
        log.info("Completed %s: %s", key, result.state)

        await store.update_state(issue.id, result.state.value)

        if result.state in (
            IssueState.COMPLETED,
            IssueState.FAILED,
            IssueState.ESCALATED,
        ):
            state.completed_keys.add(key)

    except Exception:
        log.exception("Failed processing %s", key)
        try:
            await store.update_state(issue.id, IssueState.FAILED.value)
        except Exception:
            log.warning("Could not persist failure state for %s", key)
        state.completed_keys.add(key)


async def run_poll_cycle(
    config: Config,
    state: DaemonState,
    *,
    once: bool = False,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run one poll cycle across all configured repos.

    When *once* is True, process at most one issue inline and return.
    Otherwise, spawn concurrent tasks gated by max_concurrent.
    If *shutdown_event* is provided (or set on *state*), the cycle returns early
    when the event is set, preventing new task spawns.
    """
    shutdown_event = shutdown_event or state.shutdown_event
    max_concurrent = 1 if once else config.defaults.max_concurrent

    for repo_cfg in config.repos:
        if shutdown_event is not None and shutdown_event.is_set():
            log.info("Shutdown requested — skipping remaining repos")
            return

        if not isinstance(repo_cfg, RepoConfig):
            continue

        try:
            owner = _get_repo_owner(repo_cfg)
            repo_name = _get_repo_name(repo_cfg)
        except ValueError as exc:
            log.error("Skipping repo %s: %s", repo_cfg.path, exc)
            continue

        slug = repo_slug(owner, repo_name)
        store = await _get_or_create_store(state, slug)

        # Load completed keys from this repo's store.
        # NOTE: completed_keys grows monotonically (keys are never removed).
        # Acceptable for typical deployments; consider an LRU cap if the
        # daemon runs for months with very high issue throughput.
        completed = await store.list_terminal_issue_keys()
        state.completed_keys.update(completed)

        resolved = resolve_repo_config(repo_cfg, config)
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
                try:
                    await _process_issue_task(
                        issue,
                        resolved,
                        Path(repo_cfg.path),
                        key,
                        state,
                        slug,
                        store,
                    )
                finally:
                    state.active_issues.discard(key)
                return

            # Normal mode: spawn a concurrent task
            if shutdown_event is not None and shutdown_event.is_set():
                state.active_issues.discard(key)
                log.info("Shutdown requested — not spawning task for %s", key)
                break

            task = asyncio.create_task(
                _process_issue_task(
                    issue, resolved, Path(repo_cfg.path), key, state, slug, store
                ),
                name=f"adl:{key}",
            )
            task.add_done_callback(
                functools.partial(_on_issue_done, key, state),
            )
            state.tasks[key] = task

            if shutdown_event is not None:
                # Yield so the event loop can process signal callbacks
                # (e.g. SIGTERM setting shutdown_event) between task spawns.
                await asyncio.sleep(0)


async def drain_tasks(state: DaemonState) -> None:
    """Await all in-flight issue tasks. Used on shutdown / --once exit."""
    if not state.tasks:
        return
    log.info("Draining %d in-flight task(s)…", len(state.tasks))
    await asyncio.gather(*state.tasks.values(), return_exceptions=True)


async def _interruptible_sleep(seconds: float, shutdown_event: asyncio.Event) -> None:
    """Sleep for up to `seconds`, but wake early if `shutdown_event` is set."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def _check_legacy_state(legacy_db: Path, repos_dir: Path) -> None:
    """Warn if a global state.db exists but per-repo dirs don't."""
    if legacy_db.exists():
        has_repo_dirs = repos_dir.exists() and any(
            entry.is_dir() for entry in repos_dir.iterdir()
        )
        if not has_repo_dirs:
            log.warning(
                "Found legacy global state DB at %s but no per-repo directory at %s. "
                "Per-repo isolation is now active — old state will not be used. "
                "To migrate: copy your old state.db into "
                "~/.adl/repos/<owner>/<repo>/state.db for each repository.",
                legacy_db,
                repos_dir,
            )


async def daemon_loop(config: Config, *, once: bool = False) -> None:
    """Main daemon loop — poll, process, sleep, repeat.

    If *once* is True, run a single poll cycle (processing at most one issue)
    and return.  Useful for CI / integration testing.

    Installs SIGTERM/SIGINT handlers that gracefully drain in-flight tasks
    before exiting. A second signal force-cancels all tasks.
    """
    shutdown_event = asyncio.Event()
    state = DaemonState(shutdown_event=shutdown_event)
    poll_interval = config.defaults.poll_interval

    ADL_HOME.mkdir(parents=True, exist_ok=True)
    _check_legacy_state(ADL_HOME / "state.db", ADL_HOME / "repos")

    def _request_shutdown() -> None:
        if shutdown_event.is_set():
            log.warning("Second signal received — force-cancelling tasks")
            for task in state.tasks.values():
                task.cancel()
        else:
            log.info("Shutdown signal received — draining in-flight tasks…")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    handlers_registered = False
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _request_shutdown)
        handlers_registered = True
    except NotImplementedError:
        log.warning(
            "Signal handlers not supported on this event loop; "
            "continuing without SIGTERM/SIGINT handlers",
        )

    log.info(
        "ADL daemon starting (poll_interval=%ds, once=%s)",
        poll_interval,
        once,
    )

    try:
        while not shutdown_event.is_set():
            try:
                await run_poll_cycle(config, state, once=once)
            except Exception:
                log.exception("Poll cycle failed")

            if once:
                log.info("--once: exiting after single cycle")
                return

            await _interruptible_sleep(poll_interval, shutdown_event)
    finally:
        if handlers_registered:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError) as exc:
                    log.debug("Could not remove signal handler for %s: %s", sig, exc)
                except Exception:
                    log.exception(
                        "Unexpected error removing signal handler for %s", sig
                    )
        await drain_tasks(state)
        for store in state.stores.values():
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
