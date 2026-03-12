"""Workflow YAML loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import AgentDef
from .workflow_conditions import CONDITIONS  # noqa: F401 — re-exported for validation callers


class WorkflowLoadError(Exception):
    pass


@dataclass
class TeamMemberConfig:
    agent: str
    model_role: str = "default"


@dataclass
class StageConfig:
    ref: str
    agent: str
    type: str = "single"
    optional: bool = False
    condition: str | None = None
    loopTarget: str | None = None
    maxIterations: int = 3
    canVeto: bool = False
    reviewers: list[str] = field(default_factory=list)
    team: dict[str, TeamMemberConfig] = field(default_factory=dict)


@dataclass
class WorkflowConfig:
    id: str
    description: str
    stages: list[StageConfig]


def _parse_reviewers(raw: object, stage_ref: str) -> list[str]:
    """Normalize and validate the ``reviewers`` field from YAML."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        raise WorkflowLoadError(f"Stage '{stage_ref}': reviewers must be a list, got {type(raw).__name__}")
    if not all(isinstance(r, str) for r in raw):
        raise WorkflowLoadError(f"Stage '{stage_ref}': all reviewers entries must be strings")
    return raw


def load_workflow(path: Path) -> WorkflowConfig:
    """Load a workflow from a YAML file."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise WorkflowLoadError(f"{path.name}: invalid YAML: {e}")

    if not isinstance(raw, dict) or "stages" not in raw:
        raise WorkflowLoadError(f"{path.name}: missing 'stages' key")

    stages = []
    for s in raw["stages"]:
        team = {}
        if "team" in s:
            team = {
                role: TeamMemberConfig(**cfg)
                for role, cfg in s["team"].items()
            }
        stages.append(StageConfig(
            ref=s["ref"],
            agent=s["agent"],
            type=s.get("type", "single"),
            optional=s.get("optional", False),
            condition=s.get("condition"),
            loopTarget=s.get("loopTarget"),
            maxIterations=s.get("maxIterations", 3),
            canVeto=s.get("canVeto", False),
            reviewers=_parse_reviewers(s.get("reviewers", []), s["ref"]),
            team=team,
        ))

    return WorkflowConfig(
        id=raw["id"],
        description=raw.get("description", ""),
        stages=stages,
    )


def load_all_workflows(workflows_dir: Path) -> dict[str, WorkflowConfig]:
    """Load all workflow YAML files from a directory."""
    workflows: dict[str, WorkflowConfig] = {}
    for path in sorted(workflows_dir.glob("*.yaml")):
        wf = load_workflow(path)
        workflows[wf.id] = wf
    return workflows


def validate_workflow(
    wf: WorkflowConfig,
    agents: dict[str, AgentDef],
) -> list[str]:
    """Validate workflow references and structure. Returns list of errors."""
    errors = []
    refs = {s.ref for s in wf.stages}

    for stage in wf.stages:
        # Agent must exist — skip for team/infrastructure stages whose dispatchers
        # don't use stage.agent (team uses run_agent_team; infrastructure routes by ref).
        if stage.agent not in agents and stage.type not in ("team", "infrastructure"):
            errors.append(f"Stage '{stage.ref}': agent '{stage.agent}' not found in agents/")

        # loopTarget must reference a valid stage
        if stage.loopTarget and stage.loopTarget not in refs:
            errors.append(f"Stage '{stage.ref}': loopTarget '{stage.loopTarget}' not found")

        # Team members must reference valid agents
        for role, member in stage.team.items():
            if member.agent not in agents:
                errors.append(f"Stage '{stage.ref}' team.{role}: agent '{member.agent}' not found")

        # Optional stages must have a condition
        if stage.optional and not stage.condition:
            errors.append(f"Stage '{stage.ref}': optional stage must have a condition")

        # Condition must be registered
        if stage.condition and stage.condition not in CONDITIONS:
            errors.append(f"Stage '{stage.ref}': unknown condition '{stage.condition}'")

    return errors
