"""Workflow stage interpreter engine.

Executes workflow stages sequentially with iteration loops and escalation.
Stage dispatch is injected via StageDispatcher protocol — this keeps the
engine testable without SDK dependencies.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .models import (
    APPROVED_MARKERS,
    VERDICT_BLOCKED,
    VERDICT_CLARIFICATION_NEEDED,
    VERDICT_MAX_ITERATIONS,
    VERDICT_NEEDS_REVISION,
    VERDICT_VETOED,
    Issue,
    ReviewVerdict,
    VerdictStatus,
    WorkflowResult,
    WorkflowStatus,
)
from .review_parser import parse_review_verdict
from .workflow_conditions import CONDITIONS  # noqa: F401 — re-exported as public API
from .workflow_loader import StageConfig, WorkflowConfig

log = logging.getLogger(__name__)

_VERDICT_MARKER_RE = re.compile(r"^\s*<<<VERDICT:[A-Z_]+>>>\s*$", re.MULTILINE)

_ESCALATION_REASONS: dict[VerdictStatus, str] = {
    VerdictStatus.BLOCKED: "blocked",
    VerdictStatus.CLARIFICATION_NEEDED: "clarification_needed",
    VerdictStatus.MAX_ITERATIONS: "agent_max_iterations",
}


def _strip_verdict_markers(output: str) -> str:
    """Remove verdict marker lines from agent output for clean storage."""
    stripped = _VERDICT_MARKER_RE.sub("", output)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip()


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
    status: VerdictStatus
    feedback: str | None = None


def _parse_verdict(output: str, *, strict: bool = False) -> Verdict:
    """Parse agent output into a verdict.

    When *strict* is True (review stages), missing markers default to
    ``needs_revision`` rather than ``approved`` to prevent silently passing
    malformed reviewer output.
    """
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]

    for line in reversed(lines):
        if line in APPROVED_MARKERS:
            return Verdict(status=VerdictStatus.APPROVED)
        if line == VERDICT_NEEDS_REVISION:
            rv = parse_review_verdict(output)
            return Verdict(status=VerdictStatus.NEEDS_REVISION, feedback=rv.feedback)
        if line == VERDICT_VETOED:
            return Verdict(status=VerdictStatus.VETOED, feedback=output)
        if line == VERDICT_BLOCKED:
            return Verdict(status=VerdictStatus.BLOCKED, feedback=output)
        if line == VERDICT_CLARIFICATION_NEEDED:
            return Verdict(status=VerdictStatus.CLARIFICATION_NEEDED, feedback=output)
        if line == VERDICT_MAX_ITERATIONS:
            return Verdict(status=VerdictStatus.MAX_ITERATIONS, feedback=output)

    if strict:
        return Verdict(status=VerdictStatus.NEEDS_REVISION, feedback="No verdict marker found in output")
    return Verdict(status=VerdictStatus.APPROVED)


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
    # When a loopTarget jump happens, this tracks the rejecting stage's ref.
    # Stages re-entered via the jump don't increment their dispatch_count —
    # only the rejecting stage owns the iteration budget for its loop cycle.
    _jumped_from: str | None = None

    while stage_idx < len(workflow.stages):
        stage = workflow.stages[stage_idx]

        # Clear the loopback marker once we reach the stage that triggered it
        if _jumped_from and stage.ref == _jumped_from:
            _jumped_from = None

        is_loopback_reentry = _jumped_from is not None

        # Skip optional stages whose condition is false
        if stage.optional:
            if not evaluate_condition(stage.condition, issue):
                log.info(f"Skipping optional stage {stage.ref}: condition '{stage.condition}' is false")
                stage_idx += 1
                continue

        max_iter = max(1, stage.maxIterations or 3)
        count = dispatch_count.get(stage.ref, 0)

        if not is_loopback_reentry and count >= max_iter:
            # This stage has exhausted its iteration budget
            log.info(f"Stage {stage.ref} exhausted maxIterations={max_iter}")
            last_output = stage_outputs.get(f"_{stage.ref}_last_output", "")
            last_feedback = stage_outputs.get(f"{stage.ref}_feedback", "")
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=last_feedback or None),
                "iteration_cap",
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = _strip_verdict_markers(last_output)
                stage_idx += 1
            else:
                return WorkflowResult(status=WorkflowStatus.ESCALATED, stage=stage.ref)
            continue

        if not is_loopback_reentry:
            iteration = count + 1
            log.info(f"Stage {stage.ref} iteration {iteration}/{max_iter}")
            dispatch_count[stage.ref] = iteration
        else:
            log.info(f"Stage {stage.ref} re-entry from loopTarget (not counted)")

        # Dispatch based on stage type
        if stage.type == "team":
            output = await dispatcher.dispatch_team(stage, issue, stage_outputs)
        elif stage.reviewers is not None:
            output = await dispatcher.dispatch_multi_review(stage, issue, stage_outputs)
        elif stage.type == "infrastructure":
            output = await dispatcher.dispatch_infrastructure(stage, issue, stage_outputs)
        else:
            output = await dispatcher.dispatch_single(stage, issue, stage_outputs)

        # Stash output for potential escalation after cap (prefixed with _ so
        # _build_prompt filters it out of agent prompts)
        stage_outputs[f"_{stage.ref}_last_output"] = output

        is_review = (stage.reviewers is not None) or stage.canVeto
        verdict = _parse_verdict(output, strict=is_review)

        if verdict.status == VerdictStatus.APPROVED:
            stage_outputs[stage.ref] = output
            stage_idx += 1
            continue

        if verdict.status == VerdictStatus.VETOED and stage.canVeto:
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=verdict.feedback),
                "security_veto",
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = _strip_verdict_markers(output)
                stage_idx += 1
                continue
            return WorkflowResult(status=WorkflowStatus.VETOED, stage=stage.ref)

        if verdict.status in (VerdictStatus.BLOCKED, VerdictStatus.CLARIFICATION_NEEDED, VerdictStatus.MAX_ITERATIONS):
            reason = _ESCALATION_REASONS[verdict.status]
            human_result = await dispatcher.escalate_to_human(
                issue, stage,
                ReviewVerdict(approved=False, feedback=verdict.feedback),
                reason,
            )
            if human_result == "approve":
                stage_outputs[stage.ref] = _strip_verdict_markers(output)
                stage_idx += 1
                continue
            return WorkflowResult(status=WorkflowStatus.ESCALATED, stage=stage.ref)

        if stage.loopTarget:
            # Jump back to the target stage; track feedback for context
            target_idx = _find_stage_index(workflow, stage.loopTarget)
            stage_outputs[f"{stage.ref}_feedback"] = verdict.feedback or ""
            log.info(f"Stage {stage.ref} rejected, jumping back to {stage.loopTarget}")
            _jumped_from = stage.ref  # mark so intermediate stages don't count
            stage_idx = target_idx
            continue

        # No loopTarget — feedback stored, stay on same stage (index unchanged)
        stage_outputs[f"{stage.ref}_feedback_{iteration}"] = verdict.feedback or ""
        # stage_idx stays the same; next iteration of the while loop re-runs this stage

    return WorkflowResult(status=WorkflowStatus.COMPLETED)
