"""Agent Teams dev loop — parallel tester/developer with multi-model review."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .agent_loader import load_agents
from .agent_query import agent_query
from .models import Config, DevResult, Issue
from .multi_model import multi_model_review

log = logging.getLogger(__name__)


class MaxDevCyclesError(Exception):
    pass


@dataclass
class TeamResult:
    tests_passing: bool
    diff: str


async def run_agent_team(
    issue: Issue,
    plan: str,
    agents: dict,
    worktree: Path,
    config: Config,
    cycle: int,
) -> TeamResult:
    """Run Agent Teams: orchestrator coordinates implementation.

    Placeholder for SDK TeamCreate + Task. Runs sequentially for now.
    """
    orchestrator_prompt = (
        f"## Issue: {issue.repo} #{issue.number}\n"
        f"**{issue.title}**\n\n{issue.body}\n\n"
        f"## Plan\n{plan}\n\n"
        "Implement the plan. Run tests after making changes."
    )

    output = await agent_query(
        agent_def=agents["orchestrator"],
        prompt=orchestrator_prompt,
        worktree=worktree,
        config=config,
    )

    tests_passing = "TESTS_PASSING" in output

    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "HEAD",
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    diff = stdout.decode() if stdout else ""

    return TeamResult(tests_passing=tests_passing, diff=diff)


async def dev_loop(
    issue: Issue,
    plan: str,
    worktree: Path,
    config: Config,
) -> DevResult:
    """Run the dev loop: agent team -> multi-model review -> iterate."""
    agents = load_agents(Path(config.defaults.agents_dir))
    max_cycles = config.defaults.max_dev_cycles
    review_history: list[dict] = []

    for cycle in range(1, max_cycles + 1):
        log.info(f"Dev cycle {cycle}/{max_cycles}")

        team_result = await run_agent_team(
            issue=issue, plan=plan, agents=agents,
            worktree=worktree, config=config, cycle=cycle,
        )

        if not team_result.tests_passing:
            log.info(f"Tests failing in cycle {cycle}, retrying...")
            continue

        review = await multi_model_review(
            worktree=worktree, plan=plan, diff=team_result.diff,
            agents=agents, config=config,
        )

        review_history.append({
            "cycle": cycle,
            "approved": review.verdict.approved,
            "feedback": review.verdict.feedback,
        })

        if review.verdict.approved:
            return DevResult(
                diff=team_result.diff,
                cycles=cycle,
                review_history=review_history,
            )

        plan = f"{plan}\n\n## Review feedback (cycle {cycle}):\n{review.verdict.feedback}"

    raise MaxDevCyclesError(
        f"Dev loop not approved after {max_cycles} cycles"
    )
