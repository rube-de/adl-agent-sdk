"""Sequential plan loop — architect writes plan, reviewer approves/rejects."""

from __future__ import annotations

import logging
from pathlib import Path

from .agent_loader import load_agents
from .agent_query import agent_query
from .hooks import CommandGuard
from .models import Config, Issue, PlanResult
from .review_parser import parse_review_verdict

log = logging.getLogger(__name__)


class MaxPlanIterationsError(Exception):
    pass


def build_architect_prompt(
    issue: Issue, plan: str | None, feedback: str | None,
) -> str:
    """Build the prompt sent to the architect agent."""
    parts = [
        f"## Issue: {issue.repo} #{issue.number}",
        f"**{issue.title}**",
        f"\n{issue.body}",
    ]
    if plan:
        parts.append(f"\n## Previous Plan\n{plan}")
    if feedback:
        parts.append(f"\n## Reviewer Feedback\n{feedback}")
    return "\n".join(parts)


async def plan_loop(
    issue: Issue,
    worktree: Path,
    config: Config,
    guard: CommandGuard | None = None,
) -> PlanResult:
    """Run the plan loop: architect -> reviewer -> iterate until approved."""
    agents = load_agents(Path(config.defaults.agents_dir))
    plan = None
    feedback = None
    max_iterations = config.defaults.max_plan_iterations

    for iteration in range(1, max_iterations + 1):
        log.info(f"Plan iteration {iteration}/{max_iterations}")

        plan = await agent_query(
            agent_def=agents["architect"],
            prompt=build_architect_prompt(issue, plan, feedback),
            worktree=worktree,
            config=config,
            guard=guard,
        )

        review_output = await agent_query(
            agent_def=agents["plan_reviewer"],
            prompt=plan,
            worktree=worktree,
            config=config,
            guard=guard,
        )

        verdict = parse_review_verdict(review_output)

        if verdict.approved:
            return PlanResult(plan=plan, iterations=iteration)

        feedback = verdict.feedback
        log.info(f"Plan rejected (iteration {iteration}): {feedback[:100] if feedback else ''}...")

    raise MaxPlanIterationsError(
        f"Plan not approved after {max_iterations} iterations"
    )
