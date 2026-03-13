"""Tests for core dataclasses."""

from auto_dev_loop.models import (
    AgentDef,
    Config,
    Defaults,
    Issue,
    PlanResult,
    DevResult,
    RepoConfig,
    ResolvedRepoConfig,
    ReviewIteration,
    ReviewVerdict,
    StageState,
    StageStatus,
    TelegramConfig,
    VerdictStatus,
    WorkflowResult,
    WorkflowSelectionConfig,
    WorkflowStatus,
    VERDICT_APPROVED,
)


def test_agent_def_defaults():
    agent = AgentDef(
        name="tester",
        description="Runs tests",
        system_prompt="You are a tester.",
        tools=["Bash", "Read"],
    )
    assert agent.model_role == "default"
    assert agent.max_turns == 50


def test_issue_labels_default_empty():
    issue = Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Details")
    assert issue.labels == []
    assert issue.priority is None


def test_review_verdict_approved():
    v = ReviewVerdict(approved=True, feedback=None)
    assert v.approved is True


def test_review_verdict_rejected():
    v = ReviewVerdict(approved=False, feedback="Fix the tests")
    assert v.feedback == "Fix the tests"


def test_plan_result():
    r = PlanResult(plan="## Plan\nDo things", iterations=2)
    assert r.iterations == 2


def test_dev_result():
    r = DevResult(diff="diff --git a/foo", cycles=3)
    assert r.cycles == 3


def test_stage_state_defaults():
    s = StageState(status=StageStatus.RUNNING)
    assert s.elapsed is None
    assert s.iteration == 1


def test_workflow_result():
    r = WorkflowResult(status=WorkflowStatus.COMPLETED)
    assert r.stage is None


def test_review_iteration():
    ri = ReviewIteration(
        cycle=1, iteration=1,
        worker_output="diff", reviewer_output=VERDICT_APPROVED,
        approved=True,
    )
    assert ri.approved is True


def test_config_defaults():
    cfg = Config(
        telegram={"bot_token": "tok", "chat_id": 123},
        model_roles={"smol": "haiku", "default": "sonnet", "slow": "opus"},
        repos=[{"path": "/tmp/repo", "project_number": 1}],
    )
    assert cfg.defaults.poll_interval == 60
    assert cfg.defaults.max_dev_cycles == 5


def test_repo_config_override_fields_default_none():
    rc = RepoConfig(path="/tmp/repo", project_number=1)
    assert rc.agents_dir is None
    assert rc.workflows_dir is None
    assert rc.defaults is None
    assert rc.workflow_selection is None
    assert rc.model_roles is None


def test_repo_config_with_overrides():
    rc = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        agents_dir="./custom-agents",
        defaults={"max_dev_cycles": 3},
        model_roles={"slow": "claude-opus-4-5"},
    )
    assert rc.agents_dir == "./custom-agents"
    assert rc.defaults["max_dev_cycles"] == 3
    assert rc.model_roles["slow"] == "claude-opus-4-5"


def test_resolved_repo_config():
    tg = TelegramConfig(bot_token="tok", chat_id=1)
    resolved = ResolvedRepoConfig(
        telegram=tg,
        model_roles={"default": "sonnet"},
        defaults=Defaults(),
        workflow_selection=WorkflowSelectionConfig(),
    )
    assert resolved.defaults.poll_interval == 60
    assert resolved.model_roles["default"] == "sonnet"
    assert resolved.version == 3


def test_verdict_status_str_equality():
    """StrEnum values compare equal to their string counterparts."""
    assert VerdictStatus.APPROVED == "approved"
    assert VerdictStatus.NEEDS_REVISION == "needs_revision"


def test_workflow_status_str_equality():
    assert WorkflowStatus.COMPLETED == "completed"
    assert WorkflowStatus.VETOED == "vetoed"


def test_stage_status_str_equality():
    assert StageStatus.PENDING == "pending"
    assert StageStatus.RUNNING == "running"
