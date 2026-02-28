"""Tests for workflow stage interpreter engine."""

import pytest

from auto_dev_loop.workflow_engine import (
    execute_workflow,
    evaluate_condition,
    StageDispatcher,
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
        return "approved"


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
