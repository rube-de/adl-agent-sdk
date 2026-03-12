"""Tests for workflow YAML loading and validation."""

from pathlib import Path

import pytest

from auto_dev_loop.workflow_loader import (
    load_workflow,
    load_all_workflows,
    validate_workflow,
    WorkflowLoadError,
    StageConfig,
    TeamMemberConfig,
    WorkflowConfig,
)
from auto_dev_loop.agent_loader import load_agents
from auto_dev_loop.models import AgentDef


BUG_FIX_YAML = """\
id: bug_fix
description: Bug fix — lean planning, focused build, fast turnaround

stages:
  - ref: plan
    agent: architect

  - ref: plan_review
    agent: plan_reviewer
    loopTarget: plan
    maxIterations: 1

  - ref: dev
    type: team
    agent: orchestrator
    team:
      tester:
        agent: tester
        model_role: smol
      developer:
        agent: developer
        model_role: default

  - ref: multi_review
    agent: reviewer
    reviewers: [claude, gemini, codex]
    loopTarget: dev
    maxIterations: 3
"""

FEATURE_YAML = """\
id: feature
description: Feature implementation

stages:
  - ref: research
    agent: researcher
    optional: true
    condition: unknowns_exist

  - ref: plan
    agent: architect

  - ref: plan_review
    agent: plan_reviewer
    loopTarget: plan
    maxIterations: 2

  - ref: dev
    type: team
    agent: orchestrator
    team:
      tester:
        agent: tester
        model_role: smol
      developer:
        agent: developer
        model_role: default

  - ref: multi_review
    agent: reviewer
    reviewers: [claude, gemini, codex]
    loopTarget: dev
    maxIterations: 5

  - ref: security
    agent: security_reviewer
    optional: true
    condition: security_relevant
    canVeto: true
"""


def test_load_bug_fix_workflow(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    assert wf.id == "bug_fix"
    assert len(wf.stages) == 4
    assert wf.stages[0].ref == "plan"
    assert wf.stages[0].agent == "architect"
    assert wf.stages[0].type == "single"


def test_load_team_stage(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    dev = wf.stages[2]
    assert dev.type == "team"
    assert "tester" in dev.team
    assert dev.team["tester"].agent == "tester"
    assert dev.team["tester"].model_role == "smol"
    assert dev.team["developer"].model_role == "default"


def test_load_optional_stage(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "feature.yaml").write_text(FEATURE_YAML)
    wf = load_workflow(tmp_workflows_dir / "feature.yaml")
    research = wf.stages[0]
    assert research.optional is True
    assert research.condition == "unknowns_exist"


def test_load_veto_stage(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "feature.yaml").write_text(FEATURE_YAML)
    wf = load_workflow(tmp_workflows_dir / "feature.yaml")
    security = wf.stages[-1]
    assert security.canVeto is True


def test_load_loop_target(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    assert wf.stages[1].loopTarget == "plan"
    assert wf.stages[3].loopTarget == "dev"


def test_load_reviewers(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    assert wf.stages[3].reviewers == ["claude", "gemini", "codex"]


def test_load_reviewers_empty_list(tmp_workflows_dir: Path):
    """Explicit ``reviewers: []`` in YAML yields empty list (no external reviewers)."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", "reviewers: []")
    (tmp_workflows_dir / "empty.yaml").write_text(yaml_text)
    wf = load_workflow(tmp_workflows_dir / "empty.yaml")
    assert wf.stages[3].reviewers == []


def test_load_reviewers_scalar_string_normalized(tmp_workflows_dir: Path):
    """A scalar ``reviewers: gemini`` in YAML is normalized to ``['gemini']``."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", "reviewers: gemini")
    (tmp_workflows_dir / "scalar.yaml").write_text(yaml_text)
    wf = load_workflow(tmp_workflows_dir / "scalar.yaml")
    assert wf.stages[3].reviewers == ["gemini"]


def test_load_reviewers_invalid_type_raises(tmp_workflows_dir: Path):
    """A non-string, non-list reviewers value raises WorkflowLoadError."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", "reviewers: 42")
    (tmp_workflows_dir / "bad_type.yaml").write_text(yaml_text)
    with pytest.raises(WorkflowLoadError, match="reviewers must be a list"):
        load_workflow(tmp_workflows_dir / "bad_type.yaml")


def test_load_reviewers_non_string_entries_raises(tmp_workflows_dir: Path):
    """Reviewers list with non-string entries raises WorkflowLoadError."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", "reviewers: [gemini, 123]")
    (tmp_workflows_dir / "bad_entry.yaml").write_text(yaml_text)
    with pytest.raises(WorkflowLoadError, match="must be non-empty strings"):
        load_workflow(tmp_workflows_dir / "bad_entry.yaml")


def test_load_reviewers_empty_string_raises(tmp_workflows_dir: Path):
    """Empty string in reviewers list raises WorkflowLoadError."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", 'reviewers: [gemini, ""]')
    (tmp_workflows_dir / "empty_str.yaml").write_text(yaml_text)
    with pytest.raises(WorkflowLoadError, match="non-empty strings"):
        load_workflow(tmp_workflows_dir / "empty_str.yaml")


def test_load_reviewers_whitespace_only_raises(tmp_workflows_dir: Path):
    """Whitespace-only reviewer entry raises WorkflowLoadError."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", 'reviewers: [gemini, "  "]')
    (tmp_workflows_dir / "ws.yaml").write_text(yaml_text)
    with pytest.raises(WorkflowLoadError, match="non-empty strings"):
        load_workflow(tmp_workflows_dir / "ws.yaml")


def test_load_reviewers_strips_whitespace(tmp_workflows_dir: Path):
    """Padded reviewer names are stripped at load time."""
    yaml_text = BUG_FIX_YAML.replace("reviewers: [claude, gemini, codex]", 'reviewers: ["  gemini  ", codex]')
    (tmp_workflows_dir / "padded.yaml").write_text(yaml_text)
    wf = load_workflow(tmp_workflows_dir / "padded.yaml")
    assert wf.stages[3].reviewers == ["gemini", "codex"]


def test_load_all_workflows(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    (tmp_workflows_dir / "feature.yaml").write_text(FEATURE_YAML)
    workflows = load_all_workflows(tmp_workflows_dir)
    assert "bug_fix" in workflows
    assert "feature" in workflows


def test_load_invalid_yaml(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bad.yaml").write_text("not: [valid: yaml: {{")
    with pytest.raises(WorkflowLoadError):
        load_workflow(tmp_workflows_dir / "bad.yaml")


def test_stage_defaults():
    stage = StageConfig(ref="test", agent="tester")
    assert stage.type == "single"
    assert stage.optional is False
    assert stage.maxIterations == 3
    assert stage.canVeto is False
    assert stage.reviewers is None
    assert stage.team == {}


# --- Validation tests ---

def _make_agents(*names: str) -> dict[str, AgentDef]:
    return {
        name: AgentDef(name=name, description="", system_prompt="", tools=["Bash"])
        for name in names
    }


def test_validate_valid_workflow(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    agents = _make_agents("architect", "plan_reviewer", "orchestrator", "tester", "developer", "reviewer")
    errors = validate_workflow(wf, agents)
    assert errors == []


def test_validate_missing_agent(tmp_workflows_dir: Path):
    (tmp_workflows_dir / "bug_fix.yaml").write_text(BUG_FIX_YAML)
    wf = load_workflow(tmp_workflows_dir / "bug_fix.yaml")
    agents = _make_agents("architect")  # Missing most agents
    errors = validate_workflow(wf, agents)
    assert any("plan_reviewer" in e for e in errors)
    assert any("reviewer" in e for e in errors)


def test_validate_bad_loop_target():
    wf = WorkflowConfig(
        id="bad",
        description="",
        stages=[
            StageConfig(ref="review", agent="reviewer", loopTarget="nonexistent"),
        ],
    )
    agents = _make_agents("reviewer")
    errors = validate_workflow(wf, agents)
    assert any("nonexistent" in e for e in errors)


def test_validate_optional_without_condition():
    wf = WorkflowConfig(
        id="bad",
        description="",
        stages=[
            StageConfig(ref="opt", agent="scout", optional=True),
        ],
    )
    agents = _make_agents("scout")
    errors = validate_workflow(wf, agents)
    assert any("condition" in e for e in errors)


def test_load_real_workflows():
    """Verify all workflow YAML files in workflows/ are valid."""
    workflows_dir = Path(__file__).parent.parent / "workflows"
    if not workflows_dir.exists() or not list(workflows_dir.glob("*.yaml")):
        pytest.skip("workflows/ directory not populated yet")
    workflows = load_all_workflows(workflows_dir)
    expected = {"bug_fix", "feature", "documentation", "security_audit", "ops_change"}
    assert set(workflows.keys()) == expected
    for wf_id, wf in workflows.items():
        assert wf.stages, f"{wf_id} has no stages"


def test_all_workflow_agents_exist():
    """F31: Every agent referenced in workflow YAMLs must have a definition."""
    agents_dir = Path(__file__).parent.parent / "agents"
    workflows_dir = Path(__file__).parent.parent / "workflows"

    if not agents_dir.exists() or not workflows_dir.exists():
        pytest.skip("agents/ or workflows/ directory not found")

    agents = load_agents(agents_dir)
    workflows = load_all_workflows(workflows_dir)

    all_errors: list[str] = []
    for wf_id, wf in workflows.items():
        errors = validate_workflow(wf, agents)
        all_errors.extend(f"{wf_id}: {e}" for e in errors)

    assert all_errors == [], "Validation errors:\n" + "\n".join(all_errors)
