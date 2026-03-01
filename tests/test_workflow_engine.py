"""Tests for workflow stage interpreter engine."""

import pytest

from auto_dev_loop.workflow_engine import (
    execute_workflow,
    evaluate_condition,
    StageDispatcher,
    _parse_verdict,
)
from auto_dev_loop.models import Issue, ReviewVerdict, WorkflowResult
from auto_dev_loop.workflow_loader import WorkflowConfig, StageConfig


def _issue(**kw) -> Issue:
    defaults = dict(id=1, number=1, repo="o/r", title="t", body="b")
    defaults.update(kw)
    return Issue(**defaults)


def _workflow(*stages: StageConfig) -> WorkflowConfig:
    return WorkflowConfig(id="test", description="test", stages=list(stages))


class FakeDispatcher(StageDispatcher):
    """Fake dispatcher that returns predetermined results."""

    def __init__(self, results: dict[str, str]):
        self._results = results  # stage_ref -> output text

    async def dispatch_single(self, stage, issue, prior_outputs):
        return self._results.get(stage.ref, "APPROVED")

    async def dispatch_team(self, stage, issue, prior_outputs):
        return self._results.get(stage.ref, "APPROVED")

    async def dispatch_multi_review(self, stage, issue, prior_outputs):
        return self._results.get(stage.ref, "APPROVED")

    async def dispatch_infrastructure(self, stage, issue, prior_outputs):
        return self._results.get(stage.ref, "APPROVED")

    async def escalate_to_human(self, issue, stage, verdict, reason):
        return "approve"


# --- Condition tests ---

def test_evaluate_unknowns_exist_true():
    issue = _issue(body="We need to investigate the auth flow")
    assert evaluate_condition("unknowns_exist", issue) is True


def test_evaluate_unknowns_exist_false():
    issue = _issue(body="Fix the typo in README")
    assert evaluate_condition("unknowns_exist", issue) is False


def test_evaluate_security_relevant_by_label():
    issue = _issue(labels=["security"])
    assert evaluate_condition("security_relevant", issue) is True


def test_evaluate_security_relevant_by_body():
    issue = _issue(body="Update authentication middleware")
    assert evaluate_condition("security_relevant", issue) is True


def test_evaluate_security_relevant_false():
    issue = _issue(body="Add README")
    assert evaluate_condition("security_relevant", issue) is False


def test_evaluate_unknown_condition():
    issue = _issue()
    assert evaluate_condition("nonexistent", issue) is False


# --- Engine tests ---

@pytest.mark.asyncio
async def test_simple_workflow_completes():
    wf = _workflow(
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="review", agent="reviewer"),
    )
    dispatcher = FakeDispatcher({"plan": "APPROVED", "review": "APPROVED"})
    result = await execute_workflow(wf, _issue(), dispatcher)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_optional_stage_skipped_when_condition_false():
    wf = _workflow(
        StageConfig(ref="research", agent="researcher", optional=True, condition="unknowns_exist"),
        StageConfig(ref="plan", agent="architect"),
    )
    dispatcher = FakeDispatcher({"plan": "APPROVED"})
    result = await execute_workflow(wf, _issue(body="Fix typo"), dispatcher)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_optional_stage_runs_when_condition_true():
    wf = _workflow(
        StageConfig(ref="research", agent="researcher", optional=True, condition="unknowns_exist"),
        StageConfig(ref="plan", agent="architect"),
    )
    dispatcher = FakeDispatcher({"research": "APPROVED", "plan": "APPROVED"})
    result = await execute_workflow(wf, _issue(body="We need to investigate this"), dispatcher)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_rejection_with_loop_target():
    call_count = {"plan": 0, "review": 0}

    class CountingDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            call_count[stage.ref] = call_count.get(stage.ref, 0) + 1
            if stage.ref == "review" and call_count["review"] == 1:
                return "## Feedback\nFix tests\n\nNEEDS_REVISION"
            return "APPROVED"

    wf = _workflow(
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="review", agent="reviewer", loopTarget="plan", maxIterations=3),
    )
    result = await execute_workflow(wf, _issue(), CountingDispatcher({}))
    assert result.status == "completed"
    assert call_count["review"] == 2  # First rejected, second approved


@pytest.mark.asyncio
async def test_max_iterations_escalates():
    class AlwaysRejectDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            if stage.ref == "review":
                return "NEEDS_REVISION"
            return "APPROVED"

        async def escalate_to_human(self, issue, stage, verdict, reason):
            return "timeout"

    wf = _workflow(
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="review", agent="reviewer", loopTarget="plan", maxIterations=2),
    )
    result = await execute_workflow(wf, _issue(), AlwaysRejectDispatcher({}))
    assert result.status == "escalated"
    assert result.stage == "review"


@pytest.mark.asyncio
async def test_veto_escalates():
    class VetoDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            if stage.ref == "security":
                return "VETOED"
            return "APPROVED"

        async def escalate_to_human(self, issue, stage, verdict, reason):
            return "reject"

    wf = _workflow(
        StageConfig(ref="dev", agent="developer"),
        StageConfig(ref="security", agent="sec_reviewer", canVeto=True),
    )
    result = await execute_workflow(wf, _issue(), VetoDispatcher({}))
    assert result.status == "vetoed"
    assert result.stage == "security"


def test_conditions_registry_matches_loader():
    """F36: Ensure engine and loader share the same condition names."""
    from auto_dev_loop.workflow_engine import CONDITIONS
    from auto_dev_loop.workflow_loader import CONDITIONS as LOADER_CONDITIONS
    assert CONDITIONS == LOADER_CONDITIONS


@pytest.mark.asyncio
async def test_team_stage_dispatches():
    wf = _workflow(
        StageConfig(
            ref="dev", agent="orchestrator", type="team",
            team={"tester": {"agent": "tester", "model_role": "smol"}},
        ),
    )
    dispatcher = FakeDispatcher({"dev": "APPROVED"})
    result = await execute_workflow(wf, _issue(), dispatcher)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_loop_target_reruns_target_stage():
    """F11: loopTarget should jump back and re-run the target stage."""
    call_log = []

    class TrackingDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            call_log.append(stage.ref)
            if stage.ref == "review" and call_log.count("review") == 1:
                return "## Feedback\nFix the plan\n\nNEEDS_REVISION"
            return "APPROVED"

    wf = _workflow(
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="review", agent="reviewer", loopTarget="plan", maxIterations=3),
    )
    result = await execute_workflow(wf, _issue(), TrackingDispatcher({}))
    assert result.status == "completed"
    # Expect: plan -> review (reject) -> plan (re-run) -> review (approve)
    assert call_log == ["plan", "review", "plan", "review"]


@pytest.mark.asyncio
async def test_infrastructure_stage_dispatches():
    """Infrastructure stages should call dispatch_infrastructure."""
    dispatched = []

    class InfraDispatcher(FakeDispatcher):
        async def dispatch_infrastructure(self, stage, issue, prior_outputs):
            dispatched.append(stage.ref)
            return "APPROVED"

    wf = _workflow(
        StageConfig(ref="dev", agent="developer"),
        StageConfig(ref="create_pr", agent="_infra", type="infrastructure"),
    )
    result = await execute_workflow(wf, _issue(), InfraDispatcher({}))
    assert result.status == "completed"
    assert dispatched == ["create_pr"]


@pytest.mark.asyncio
async def test_loopback_does_not_count_target_stage():
    """Verify that loopTarget re-entries don't increment the target stage's dispatch count.

    Without the fix, plan would exhaust its budget (count=3) before review (count=2),
    causing an incorrect escalation at 'plan' instead of 'review'.
    """
    call_count = {"plan": 0, "review": 0}
    escalated_at = {}

    class CountingDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            call_count[stage.ref] = call_count.get(stage.ref, 0) + 1
            if stage.ref == "review":
                return "## Feedback\nBad plan\n\nNEEDS_REVISION"
            return "APPROVED"

        async def escalate_to_human(self, issue, stage, verdict, reason):
            escalated_at["stage"] = stage.ref
            return "timeout"

    wf = _workflow(
        StageConfig(ref="plan", agent="architect", maxIterations=3),
        StageConfig(ref="review", agent="reviewer", loopTarget="plan", maxIterations=3),
    )
    result = await execute_workflow(wf, _issue(), CountingDispatcher({}))
    assert result.status == "escalated"
    # The rejecting stage (review) should escalate, not the target (plan)
    assert escalated_at["stage"] == "review"
    # review ran 3 times (its maxIterations)
    assert call_count["review"] == 3
    # plan ran 1 + 3 re-entries = 4 times, but only counted once
    assert call_count["plan"] == 4


@pytest.mark.asyncio
async def test_internal_keys_prefixed_with_underscore():
    """Verify that _last_output keys are prefixed with _ so _build_prompt filters them."""
    seen_keys: list[list[str]] = []

    class SpyDispatcher(FakeDispatcher):
        async def dispatch_single(self, stage, issue, prior_outputs):
            seen_keys.append(list(prior_outputs.keys()))
            return "APPROVED"

    wf = _workflow(
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="review", agent="reviewer"),
    )
    await execute_workflow(wf, _issue(), SpyDispatcher({}))
    review_keys = seen_keys[1]  # second dispatch sees prior outputs
    # Approved output stored under bare ref
    assert "plan" in review_keys
    # Internal _last_output key uses _ prefix (so _build_prompt filters it)
    internal_keys = [k for k in review_keys if "last_output" in k]
    assert all(k.startswith("_") for k in internal_keys), \
        f"Internal keys should be _-prefixed: {internal_keys}"


# --- _parse_verdict tests ---

def test_parse_verdict_approved_beyond_5_lines():
    """Verdict keyword beyond last 5 lines should still be found."""
    verbose_lines = "\n".join(f"Detail line {i}" for i in range(10))
    output = f"APPROVED\n{verbose_lines}"
    verdict = _parse_verdict(output)
    assert verdict.status == "approved"


def test_parse_verdict_needs_revision_beyond_5_lines():
    """NEEDS_REVISION beyond last 5 lines should still be found."""
    verbose_lines = "\n".join(f"Detail line {i}" for i in range(10))
    output = f"## Feedback\nFix the bug\n\nNEEDS_REVISION\n{verbose_lines}"
    verdict = _parse_verdict(output)
    assert verdict.status == "needs_revision"


def test_parse_verdict_strict_no_marker():
    """Strict mode with no marker should default to needs_revision."""
    verdict = _parse_verdict("Some output with no verdict keyword", strict=True)
    assert verdict.status == "needs_revision"
    assert verdict.feedback is not None


def test_parse_verdict_nonstrict_no_marker():
    """Non-strict mode with no marker should default to approved."""
    verdict = _parse_verdict("Some output with no verdict keyword", strict=False)
    assert verdict.status == "approved"
