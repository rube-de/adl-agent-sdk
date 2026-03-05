"""Core dataclasses for Auto Dev Loop."""

from __future__ import annotations

from dataclasses import dataclass, field

# Verdict markers emitted by agents and parsed by the workflow engine.
# Keep in sync: dispatcher produces these, workflow_engine/review_parser consume them.
VERDICT_APPROVED = "<<<VERDICT:APPROVED>>>"
VERDICT_NEEDS_REVISION = "<<<VERDICT:NEEDS_REVISION>>>"
VERDICT_VETOED = "<<<VERDICT:VETOED>>>"
VERDICT_PLAN_READY = "<<<VERDICT:PLAN_READY>>>"
VERDICT_TESTS_PASSING = "<<<VERDICT:TESTS_PASSING>>>"
VERDICT_IMPLEMENTATION_COMPLETE = "<<<VERDICT:IMPLEMENTATION_COMPLETE>>>"
VERDICT_FIXES_APPLIED = "<<<VERDICT:FIXES_APPLIED>>>"
VERDICT_FEEDBACK_APPLIED = "<<<VERDICT:FEEDBACK_APPLIED>>>"

# Issue states that represent terminal (finished) processing.
TERMINAL_ISSUE_STATES = frozenset({"completed", "failed", "escalated"})

# Set of markers that the engine treats as "approved" (stage passes).
APPROVED_MARKERS = frozenset({
    VERDICT_APPROVED,
    VERDICT_PLAN_READY,
    VERDICT_TESTS_PASSING,
    VERDICT_IMPLEMENTATION_COMPLETE,
    VERDICT_FIXES_APPLIED,
    VERDICT_FEEDBACK_APPLIED,
})


def has_verdict_line(output: str, marker: str) -> bool:
    """Check if a verdict marker appears on its own line in output."""
    return any(line.strip() == marker for line in output.splitlines())


@dataclass
class AgentDef:
    name: str
    description: str
    system_prompt: str
    tools: list[str]
    model_role: str = "default"
    max_turns: int = 50


@dataclass
class Issue:
    id: int
    number: int
    repo: str
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    priority: str | None = None
    project_item_id: str | None = None


@dataclass
class ReviewVerdict:
    approved: bool
    feedback: str | None


@dataclass
class ReviewIteration:
    cycle: int
    iteration: int
    worker_output: str
    reviewer_output: str
    approved: bool


@dataclass
class PlanResult:
    plan: str
    iterations: int


@dataclass
class DevResult:
    diff: str
    cycles: int
    review_history: list[dict] = field(default_factory=list)


@dataclass
class StageState:
    status: str  # "pending", "running", "approved", "completed", "vetoed", "escalated"
    elapsed: str | None = None
    iteration: int = 1
    started_at: float | None = None


@dataclass
class WorkflowResult:
    status: str  # "completed", "vetoed", "escalated"
    stage: str | None = None


@dataclass
class Defaults:
    agents_dir: str = "./agents"
    workflows_dir: str = "./workflows"
    poll_interval: int = 60
    max_concurrent: int = 1
    max_plan_iterations: int = 3
    max_dev_cycles: int = 5
    max_review_cycles: int = 5
    review_backoff: list[int] = field(default_factory=lambda: [120, 300, 900, 1800, 3600])
    worker_timeout: int = 3600
    external_review_timeout: int = 300
    circuit_breaker_failures: int = 3
    plan_reviewers: list[str] = field(default_factory=list)
    external_reviewers: list[str] = field(default_factory=lambda: ["gemini"])


@dataclass
class RepoConfig:
    path: str
    project_number: int
    columns: dict[str, str] = field(default_factory=lambda: {
        "source": "Ready for Dev",
        "in_progress": "In Progress",
        "done": "Done",
    })
    owner: str | None = None


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: int
    chat_type: str = "private"
    human_timeout: int = 3600
    progress_updates: bool = True


@dataclass
class WorkflowSelectionConfig:
    default: str = "feature"
    label_map: dict[str, str] = field(default_factory=dict)
    priority_overrides: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Config:
    telegram: dict | TelegramConfig
    model_roles: dict[str, str]
    repos: list[dict | RepoConfig]
    version: int = 3
    defaults: Defaults = field(default_factory=Defaults)
    workflow_selection: WorkflowSelectionConfig = field(default_factory=WorkflowSelectionConfig)
