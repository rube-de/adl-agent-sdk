"""Concrete StageDispatcher — bridges workflow engine to agent/infrastructure functions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .agent_query import agent_query
from .branch import build_branch_name  # re-exported as public API
from .dev_loop import run_agent_team
from .hooks import CommandGuard
from .models import (
    VERDICT_APPROVED,
    VERDICT_NEEDS_REVISION,
    VERDICT_TESTS_PASSING,
    Config,
    Issue,
    ReviewVerdict,
    fence_untrusted,
)
from .multi_model import multi_model_review
from .pr import create_pr
from .review_loop import review_loop
from .workflow_engine import StageDispatcher
from .workflow_loader import StageConfig

if TYPE_CHECKING:
    from .telegram import TelegramBot

log = logging.getLogger(__name__)

__all__ = ["OrchestratorDispatcher", "build_branch_name"]


class OrchestratorDispatcher(StageDispatcher):
    """Concrete dispatcher wiring workflow stages to agent_query, teams, and infrastructure."""

    def __init__(
        self,
        agents: dict,
        config: Config,
        worktree: Path,
        guard: CommandGuard | None,
        telegram: TelegramBot | None,
        issue: Issue,
    ) -> None:
        self._agents = agents
        self._config = config
        self._worktree = worktree
        self._guard = guard
        self._telegram = telegram
        self._issue = issue
        self.pr_number: int | None = None

    # --- Prompt builders ---

    def _build_prompt(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str:
        """Build a prompt from issue context plus prior stage outputs."""
        parts = [
            f"## Issue: {issue.repo} #{issue.number}",
            f"**{fence_untrusted(issue.title, 'issue-title')}**",
            "",
            fence_untrusted(issue.body, "issue-body"),
        ]

        for ref, output in prior_outputs.items():
            if ref.endswith("_feedback") or "_feedback_" in ref:
                parts.append(f"\n## Feedback\n{output}")
            elif ref == "plan":
                parts.append(f"\n## Plan\n{output}")
            elif ref == "research":
                parts.append(f"\n## Research\n{output}")
            elif not ref.startswith("_"):
                parts.append(f"\n## {ref} output\n{output}")

        return "\n".join(parts)

    # --- Dispatch methods ---

    async def dispatch_single(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str:
        agent_def = self._agents[stage.agent]
        prompt = self._build_prompt(stage, issue, prior_outputs)
        return await agent_query(
            agent_def=agent_def,
            prompt=prompt,
            worktree=self._worktree,
            config=self._config,
            guard=self._guard,
        )

    async def dispatch_team(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str:
        plan = prior_outputs.get("plan", "")
        team_result = await run_agent_team(
            issue=issue,
            plan=plan,
            agents=self._agents,
            worktree=self._worktree,
            config=self._config,
            cycle=1,
            guard=self._guard,
        )
        if team_result.tests_passing:
            return f"{team_result.diff}\n\n{VERDICT_TESTS_PASSING}\n{VERDICT_APPROVED}"
        return f"{team_result.diff}\n\n{VERDICT_NEEDS_REVISION}"

    async def dispatch_multi_review(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str:
        plan = prior_outputs.get("plan", "")
        dev_output = prior_outputs.get("dev", "")
        diff = dev_output.partition(f"\n\n{VERDICT_TESTS_PASSING}")[0]
        review = await multi_model_review(
            worktree=self._worktree,
            plan=plan,
            diff=diff,
            agents=self._agents,
            config=self._config,
        )
        if review.verdict.approved:
            return VERDICT_APPROVED
        return f"## Feedback\n{review.verdict.feedback}\n\n{VERDICT_NEEDS_REVISION}"

    async def dispatch_infrastructure(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str:
        if stage.ref == "create_pr":
            self.pr_number = await create_pr(issue, self._worktree)
            log.info("PR #%d created", self.pr_number)
            return f"PR #{self.pr_number} created\n\n{VERDICT_APPROVED}"

        if stage.ref == "pr_review":
            if self.pr_number is None:
                raise RuntimeError("pr_review stage requires create_pr to run first")
            result = await review_loop(
                issue,
                self.pr_number,
                self._worktree,
                self._config,
                guard=self._guard,
            )
            log.info("Review completed after %d cycles", result.cycles)
            return f"Review completed ({result.cycles} cycles)\n\n{VERDICT_APPROVED}"

        raise ValueError(f"Unknown infrastructure stage: {stage.ref}")

    async def escalate_to_human(
        self,
        issue: Issue,
        stage: StageConfig,
        verdict: ReviewVerdict,
        reason: str,
    ) -> str:
        if self._telegram is None:
            log.warning("No Telegram bot for escalation, auto-approving %s", stage.ref)
            return "approve"

        decision = await self._telegram.escalate(issue, stage, verdict, reason)
        return decision.action
