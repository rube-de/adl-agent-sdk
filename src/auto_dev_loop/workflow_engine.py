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
from .workflow_conditions import CONDITIONS  # noqa: F401 — re-exported as public API
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
    async def dispatch_infrastructure(
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
    evaluator = CONDITIONS.get(condition)
    if evaluator is None:
        log.warning(f"Unknown condition '{condition}', treating as False")
        return False
    return evaluator(issue)


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


def _find_stage_index(workflow: WorkflowConfig, ref: str) -> int:
    """Find the index of a stage by ref."""
    for i, stage in enumerate(workflow.stages):
        if stage.ref == ref:
            return i
    raise ValueError(f"Stage '{ref}' not found in workflow '{workflow.id}'")


async def execute_workflow(
    workflow: WorkflowConfig,
    issue: Issue,
    dispatcher: StageDispatcher,
) -> WorkflowResult:
    """Execute a workflow stage by stage with loopTarget support.

    Uses a while-loop with an index pointer so that loopTarget can jump
    backwards. dispatch_count tracks total dispatches per stage across all
    passes so that maxIterations is enforced globally, not per inner loop.
    """
    stage_outputs: dict[str, str] = {}
    dispatch_count: dict[str, int] = {}
    stage_idx = 0

    while stage_idx < len(workflow.stages):
        stage = workflow.stages[stage_idx]

        # Skip optional stages whose condition is false
        if stage.optional:
            if not evaluate_condition(stage.condition, issue):
                log.info(f"Skipping optional stage {stage.ref}: condition '{stage.condition}' is false")
                stage_idx += 1
                continue

        max_iter = stage.maxIterations or 3
        count = dispatch_count.get(stage.ref, 0)

        if count >= max_iter:
            # This stage has exhausted its iteration budget across all loop-back passes
            log.info(f"Stage {stage.ref} exhausted maxIterations={max_iter}")
            last_output = stage_outputs.get(f"{stage.ref}_last_output", "")
            last_feedback = stage_outputs.get(f"{stage.ref}_feedback", "")
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=last_feedback or None),
                "iteration_cap",
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = last_output
                stage_idx += 1
            else:
                return WorkflowResult(status="escalated", stage=stage.ref)
            continue

        iteration = count + 1
        log.info(f"Stage {stage.ref} iteration {iteration}/{max_iter}")
        dispatch_count[stage.ref] = iteration

        # Dispatch based on stage type
        if stage.type == "team":
            output = await dispatcher.dispatch_team(stage, issue, stage_outputs)
        elif stage.reviewers:
            output = await dispatcher.dispatch_multi_review(stage, issue, stage_outputs)
        elif stage.type == "infrastructure":
            output = await dispatcher.dispatch_infrastructure(stage, issue, stage_outputs)
        else:
            output = await dispatcher.dispatch_single(stage, issue, stage_outputs)

        # Stash output for potential escalation after cap
        stage_outputs[f"{stage.ref}_last_output"] = output

        verdict = _parse_verdict(output)

        if verdict.status in ("approved", "completed"):
            stage_outputs[stage.ref] = output
            stage_idx += 1
            continue

        if verdict.status == "vetoed" and stage.canVeto:
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=verdict.feedback),
                "security_veto",
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = output
                stage_idx += 1
                continue
            return WorkflowResult(status="vetoed", stage=stage.ref)

        if stage.loopTarget:
            # Jump back to the target stage; track feedback for context
            target_idx = _find_stage_index(workflow, stage.loopTarget)
            stage_outputs[f"{stage.ref}_feedback"] = verdict.feedback or ""
            log.info(f"Stage {stage.ref} rejected, jumping back to {stage.loopTarget}")
            stage_idx = target_idx
            continue

        # No loopTarget — feedback stored, stay on same stage (index unchanged)
        stage_outputs[f"{stage.ref}_feedback_{iteration}"] = verdict.feedback or ""
        # stage_idx stays the same; next iteration of the while loop re-runs this stage

    return WorkflowResult(status="completed")
