"""PR state, review status, and CI checking via gh CLI."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class PrStatus:
    state: str
    mergeable: str
    review_approved: bool
    ci_passing: bool
    review_comments: list[str] = field(default_factory=list)

    @property
    def ready_to_merge(self) -> bool:
        return self.state == "OPEN" and self.review_approved and self.ci_passing


def parse_pr_status(data: dict) -> PrStatus:
    """Parse gh pr view --json output."""
    checks = data.get("statusCheckRollup", [])
    ci_passing = all(
        c.get("state") == "SUCCESS" or c.get("conclusion") == "SUCCESS"
        for c in checks
    ) if checks else True

    return PrStatus(
        state=data.get("state", "OPEN"),
        mergeable=data.get("mergeable", "UNKNOWN"),
        review_approved=data.get("reviewDecision") == "APPROVED",
        ci_passing=ci_passing,
    )


async def check_pr_status(repo: str, pr_number: int) -> PrStatus:
    """Check PR status via gh CLI."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "view", str(pr_number),
        "--repo", repo,
        "--json", "state,mergeable,reviewDecision,statusCheckRollup",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        log.error(f"gh pr view failed: {stderr.decode()}")
        return PrStatus(
            state="UNKNOWN", mergeable="UNKNOWN",
            review_approved=False, ci_passing=False,
        )

    return parse_pr_status(json.loads(stdout))
