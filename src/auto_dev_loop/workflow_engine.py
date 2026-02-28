"""Workflow stage interpreter engine.

Executes workflow stages sequentially with iteration loops and escalation.
Stage dispatch is injected via StageDispatcher protocol — this keeps the
engine testable without SDK dependencies.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .models import Issue, ReviewVerdict, WorkflowResult
from .review_parser import parse_review_verdict
from .workflow_loader import StageConfig, WorkflowConfig

log = logging.getLogger(__name__)


class StageDispatcher(ABC):
    """Protocol for stage dispatch. Implemented by SDK integration layer."""

    @abstractmethod
    async def dispatch_single(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str: ...

    @abstractmethod
    async def dispatch_team(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str: ...

    @abstractmethod
    async def dispatch_multi_review(
        self,
        stage: StageConfig,
        issue: Issue,
        prior_outputs: dict[str, str],
    ) -> str: ...

    @abstractmethod
    async def escalate_to_human(
        self,
        issue: Issue,
        stage: StageConfig,
        verdict: ReviewVerdict,
        reason: str,
    ) -> str: ...


# --- Named conditions ---

def evaluate_condition(condition: str, issue: Issue) -> bool:
    """Evaluate a named condition against an issue."""
    evaluator = _CONDITIONS.get(condition)
    if evaluator is None:
        log.warning(f"Unknown condition '{condition}', treating as False")
        return False
    return evaluator(issue)


def _unknowns_exist(issue: Issue) -> bool:
    return (
        "?" in issue.body
        or any(
            w in issue.body.lower()
            for w in ("unclear", "unknown", "investigate", "explore")
        )
    )


def _security_relevant(issue: Issue) -> bool:
    return (
        "security" in issue.labels
        or any(
            w in issue.body.lower()
            for w in ("auth", "crypto", "permissions", "cve", "vulnerability")
        )
    )


def _deployment_needed(issue: Issue) -> bool:
    return any(label in issue.labels for label in ("deploy", "ops", "infrastructure"))


def _code_review_needed(issue: Issue) -> bool:
    return True


_CONDITIONS: dict[str, callable] = {
    "unknowns_exist": _unknowns_exist,
    "security_relevant": _security_relevant,
    "deployment_needed": _deployment_needed,
    "code_review_needed": _code_review_needed,
}


# --- Engine ---

@dataclass
class Verdict:
    status: str  # "approved", "completed", "needs_revision", "vetoed"
    feedback: str | None = None


def _parse_verdict(output: str) -> Verdict:
    """Parse agent output into a verdict."""
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]

    for line in reversed(lines[-5:]):
        if line in ("APPROVED", "PLAN_READY", "TESTS_PASSING",
                     "IMPLEMENTATION_COMPLETE", "FIXES_APPLIED",
                     "FEEDBACK_APPLIED"):
            return Verdict(status="approved")
        if line == "NEEDS_REVISION":
            rv = parse_review_verdict(output)
            return Verdict(status="needs_revision", feedback=rv.feedback)
        if line == "VETOED":
            return Verdict(status="vetoed", feedback=output)

    # No clear marker — treat as completed (non-review stages)
    return Verdict(status="approved")


async def execute_workflow(
    workflow: WorkflowConfig,
    issue: Issue,
    dispatcher: StageDispatcher,
) -> WorkflowResult:
    """Execute a workflow stage by stage."""
    stage_outputs: dict[str, str] = {}

    for stage in workflow.stages:
        # Skip optional stages whose condition is false
        if stage.optional:
            if not evaluate_condition(stage.condition, issue):
                log.info(f"Skipping optional stage {stage.ref}: condition '{stage.condition}' is false")
                continue

        for iteration in range(1, (stage.maxIterations or 3) + 1):
            log.info(f"Stage {stage.ref} iteration {iteration}/{stage.maxIterations}")

            # Dispatch
            if stage.type == "team":
                output = await dispatcher.dispatch_team(stage, issue, stage_outputs)
            elif stage.reviewers:
                output = await dispatcher.dispatch_multi_review(stage, issue, stage_outputs)
            else:
                output = await dispatcher.dispatch_single(stage, issue, stage_outputs)

            verdict = _parse_verdict(output)

            if verdict.status in ("approved", "completed"):
                stage_outputs[stage.ref] = output
                break

            if verdict.status == "vetoed" and stage.canVeto:
                human_result = await dispatcher.escalate_to_human(
                    issue, stage,
                    ReviewVerdict(approved=False, feedback=verdict.feedback),
                    "security_veto",
                )
                if human_result == "approve":
                    stage_outputs[stage.ref] = output
                    break
                return WorkflowResult(status="vetoed", stage=stage.ref)

            if stage.loopTarget:
                # Inject feedback for next iteration
                stage_outputs[f"{stage.ref}_feedback_{iteration}"] = verdict.feedback or ""
                log.info(f"Stage {stage.ref} rejected, re-dispatching {stage.loopTarget}")
        else:
            # Max iterations exhausted
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=verdict.feedback),
                "iteration_cap",
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = output
                continue
            return WorkflowResult(status="escalated", stage=stage.ref)

    return WorkflowResult(status="completed")
