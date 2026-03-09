"""Parallel multi-model review — Claude + external reviewers via asyncio.gather."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .agent_query import agent_query
from .models import AgentDef, AppConfig, ReviewVerdict, VERDICT_APPROVED, VERDICT_NEEDS_REVISION
from .review_parser import parse_review_verdict, synthesize_reviews

log = logging.getLogger(__name__)


class AllReviewersFailedError(Exception):
    pass


@dataclass
class MultiModelReviewResult:
    verdict: ReviewVerdict
    individual: list[tuple[str, ReviewVerdict]] = field(default_factory=list)


def build_review_prompt(plan: str, diff: str) -> str:
    """Build the code review prompt with plan context and diff."""
    return (
        f"## Implementation Plan\n{plan}\n\n"
        f"## Code Changes\n```diff\n{diff}\n```\n\n"
        f"Review the code changes against the plan. "
        f"End with {VERDICT_APPROVED} or {VERDICT_NEEDS_REVISION}."
    )


async def run_external_with_timeout(
    cmd: str, prompt: str, worktree: Path, timeout: float,
) -> str:
    """Run an external reviewer subprocess with timeout."""
    proc = await asyncio.create_subprocess_exec(
        cmd, "--prompt", prompt,
        cwd=str(worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"External reviewer {cmd} failed: {stderr.decode()}")
    return stdout.decode()


async def multi_model_review(
    worktree: Path,
    plan: str,
    diff: str,
    agents: dict[str, AgentDef],
    config: AppConfig,
) -> MultiModelReviewResult:
    """Run parallel multi-model review. Conservative: any rejection = reject."""
    review_prompt = build_review_prompt(plan, diff)
    external_reviewers = config.defaults.external_reviewers
    review_timeout = config.defaults.external_review_timeout

    tasks = [
        agent_query(
            agent_def=agents["reviewer"],
            prompt=review_prompt,
            worktree=worktree,
            config=config,
        ),
    ]
    for cmd in external_reviewers:
        tasks.append(
            run_external_with_timeout(cmd, review_prompt, worktree, timeout=review_timeout)
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    reviews: list[tuple[str, ReviewVerdict]] = []
    for i, result in enumerate(results):
        model_name = "claude" if i == 0 else external_reviewers[i - 1]
        if isinstance(result, Exception):
            log.warning(f"Reviewer {model_name} failed: {result}")
            continue
        verdict = parse_review_verdict(result)
        reviews.append((model_name, verdict))

    if not reviews:
        raise AllReviewersFailedError("All reviewers failed or timed out")

    return MultiModelReviewResult(
        verdict=synthesize_reviews(reviews),
        individual=reviews,
    )
