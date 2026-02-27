# Workflow Abstraction Layer — Auto Dev Loop v3

> Declarative YAML workflow definitions that route different issue types to different agent pipelines. Inspired by [ClawControl](https://github.com/salexandr0s/clawcontrol)'s stage engine + pi ecosystem research.

**Date:** 2026-02-27
**Status:** Approved
**Applies to:** Both Python/Claude Agent SDK v3 and TypeScript/oh-my-pi v3 designs

---

## 0. Motivation

The v3 designs hardcode a single pipeline: `plan → dev → review → PR`. But different issue types need different flows:

- **Bug fixes** need less planning (1 iteration), focused dev, fast turnaround
- **Features** need research, multi-iteration planning, thorough review
- **Documentation** doesn't need a dev team — single agent writes, reviewer checks
- **Security audits** are security-first with veto authority
- **Ops changes** need security sign-off before deployment

Abstracting workflows into YAML files makes the pipeline data-driven instead of code-driven.

---

## 1. Two-Layer Architecture

```
Layer 1: Workflow YAML (macro)         Layer 2: DAG/Teams (micro)
┌──────────────────────┐
│ bug_fix.yaml         │
│                      │
│ stage: plan          │──→ single agent session
│ stage: plan_review   │──→ single agent session (loopTarget: plan)
│ stage: dev           │──→ ┌─────────────────────────┐
│   type: team         │    │ Agent Teams (Python) or  │
│                      │    │ pi-parallel-agents (TS)  │
│                      │    │                          │
│                      │    │ test(smol)               │
│                      │    │   → fix(default)         │
│                      │    │   → verify(smol)         │
│                      │    └─────────────────────────┘
│ stage: multi_review  │──→ multi-model review (claude + gemini + codex)
│ stage: security      │──→ single agent session (optional, canVeto)
└──────────────────────┘
```

- **Workflow YAML** = "what stages to run for this issue type, in what order"
- **DAG / Agent Teams** = "how agents coordinate within a team stage"

The workflow engine handles macro orchestration (stage sequencing, iteration loops, escalation). The DAG/Agent Teams handle micro coordination within `type: team` stages.

---

## 2. Workflow YAML Schema

### Stage Schema

```yaml
stages:
  - ref: string               # unique stage identifier within workflow
    agent: string             # agent definition name (from agents/*.md)
    type: single | team       # default: single
    optional: bool            # default: false — skip if condition is false
    condition: string         # named condition function
    loopTarget: string        # stage ref to re-dispatch on rejection
    maxIterations: int        # iteration cap before escalation (default: 3)
    canVeto: bool             # if true, VETOED = escalate to human
    reviewers: string[]       # for multi-model review stages (model names)
    team:                     # only when type: team
      <role>:                 # role name (tester, developer, verifier)
        agent: string         # agent definition name
        model_role: string    # smol | default | slow
```

### Stage Types

| Type | Dispatch | Result parsing |
|------|----------|---------------|
| `single` | One agent session via `agent_query()` / `runAgent()` | Parse `APPROVED` / `NEEDS_REVISION` / `VETOED` from output |
| `team` | DAG (TS: pi-parallel-agents) or Agent Teams (Python: SDK) | Parse from team coordinator output |

### Stage Results

Parsed from agent output (same `APPROVED` / `NEEDS_REVISION` parsing from v3):

| Result | Action |
|--------|--------|
| `APPROVED` / `COMPLETED` | Advance to next stage |
| `NEEDS_REVISION` | Re-dispatch `loopTarget` stage with feedback |
| `VETOED` (only if `canVeto: true`) | Escalate to human via Telegram |
| Iteration cap hit | Escalate to human via Telegram |

### Named Conditions

Simple predicate functions evaluated against issue context:

```python
CONDITIONS = {
    "unknowns_exist": lambda ctx: (
        "?" in ctx.body
        or any(w in ctx.body.lower() for w in ["unclear", "unknown", "investigate", "explore"])
    ),
    "security_relevant": lambda ctx: (
        "security" in ctx.labels
        or any(w in ctx.body.lower() for w in ["auth", "crypto", "permissions", "cve", "vulnerability"])
    ),
    "deployment_needed": lambda ctx: (
        any(l in ctx.labels for l in ["deploy", "ops", "infrastructure"])
    ),
    "code_review_needed": lambda ctx: True,  # always true unless overridden
}
```

Not an expression language. Named functions registered in code. New conditions require a code change — this is intentional (prevents YAML injection, keeps logic testable).

---

## 3. Pre-Built Workflows

### `bug_fix.yaml`

```yaml
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
      verifier:
        agent: tester
        model_role: smol

  - ref: multi_review
    agent: reviewer
    reviewers: [claude, gemini, codex]
    loopTarget: dev
    maxIterations: 3
```

### `feature.yaml`

```yaml
id: feature
description: Feature implementation — research, planning, iterative dev, thorough review

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
      verifier:
        agent: tester
        model_role: smol

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
```

### `documentation.yaml`

```yaml
id: documentation
description: Documentation — plan, write, review (no dev team needed)

stages:
  - ref: plan
    agent: architect

  - ref: plan_review
    agent: plan_reviewer
    loopTarget: plan
    maxIterations: 1

  - ref: write
    agent: developer
    # type: single — one agent writes docs, no team coordination needed

  - ref: review
    agent: reviewer
    reviewers: [claude]
    loopTarget: write
    maxIterations: 2
```

### `security_audit.yaml`

```yaml
id: security_audit
description: Security audit — security-first with veto authority

stages:
  - ref: security
    agent: security_reviewer
    canVeto: true

  - ref: code_review
    agent: reviewer
    optional: true
    condition: code_review_needed
```

### `ops_change.yaml`

```yaml
id: ops_change
description: Infrastructure/ops changes — plan, implement, security gate, deploy

stages:
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
      verifier:
        agent: tester
        model_role: smol

  - ref: security
    agent: security_reviewer
    canVeto: true
    loopTarget: dev
    maxIterations: 1
```

---

## 4. Workflow Routing

### Label-Based Selection

```yaml
# In config: auto-dev.yaml
workflow_selection:
  default: feature

  label_map:
    bug: bug_fix
    hotfix: bug_fix
    regression: bug_fix
    feature: feature
    enhancement: feature
    docs: documentation
    documentation: documentation
    security: security_audit
    audit: security_audit
    infrastructure: ops_change
    deploy: ops_change
    ops: ops_change

  priority_overrides:
    P0:
      bug: bug_fix
      security: security_audit
```

### Resolution Logic

```python
def select_workflow(issue: Issue, config: Config) -> str:
    """Select workflow ID from issue metadata. First match wins."""
    labels = set(issue.labels)

    # 1. Priority overrides
    if issue.priority in config.workflow_selection.priority_overrides:
        overrides = config.workflow_selection.priority_overrides[issue.priority]
        for label in labels:
            if label in overrides:
                return overrides[label]

    # 2. Label mapping
    for label in labels:
        if label in config.workflow_selection.label_map:
            return config.workflow_selection.label_map[label]

    # 3. Default
    return config.workflow_selection.default
```

No fuzzy keyword matching. No title scanning. Labels are explicit — if you want the bug workflow, label the issue `bug`. Clean, debuggable, zero magic.

---

## 5. Workflow Engine

### Core Loop

```python
async def execute_workflow(
    workflow: WorkflowConfig,
    issue: Issue,
    worktree: Path,
) -> WorkflowResult:
    """Execute a workflow stage by stage."""
    stage_outputs: dict[str, str] = {}

    for stage_idx, stage in enumerate(workflow.stages):
        # Skip optional stages whose condition is false
        if stage.optional:
            if not evaluate_condition(stage.condition, issue):
                log.info(f"Skipping optional stage {stage.ref}: condition '{stage.condition}' is false")
                continue

        db.update_state(issue.id, f"STAGE_{stage.ref.upper()}")

        for iteration in range(1, (stage.maxIterations or 3) + 1):
            # Dispatch stage
            if stage.type == "team":
                result = await dispatch_team_stage(stage, issue, worktree, stage_outputs)
            elif stage.reviewers:
                result = await dispatch_multi_review_stage(stage, issue, worktree, stage_outputs)
            else:
                result = await dispatch_single_stage(stage, issue, worktree, stage_outputs)

            verdict = parse_verdict(result)
            store_stage_result(issue, stage, iteration, result, verdict)

            if verdict.status in ("approved", "completed"):
                stage_outputs[stage.ref] = result
                break

            if verdict.status == "vetoed" and stage.canVeto:
                await escalate_to_human(issue, stage, verdict, "security_veto")
                return WorkflowResult(status="vetoed", stage=stage.ref)

            if stage.loopTarget:
                # Re-dispatch the target stage with feedback
                feedback = verdict.feedback
                log.info(f"Stage {stage.ref} rejected (iteration {iteration}), re-dispatching {stage.loopTarget}")
                # Inject feedback into stage_outputs for next iteration
                stage_outputs[f"{stage.ref}_feedback_{iteration}"] = feedback
        else:
            # Max iterations exhausted
            await escalate_to_human(issue, stage, verdict, "iteration_cap")
            return WorkflowResult(status="escalated", stage=stage.ref)

    return WorkflowResult(status="completed")
```

### Team Stage Dispatch (Python — Agent Teams)

```python
async def dispatch_team_stage(
    stage: StageConfig,
    issue: Issue,
    worktree: Path,
    prior_outputs: dict[str, str],
) -> str:
    """Dispatch a team stage using SDK Agent Teams."""
    agents = load_agents(config.agents_dir)

    team_result = await run_agent_team(
        issue=issue,
        plan=prior_outputs.get("plan", ""),
        agents=agents,
        worktree=worktree,
        team_config=stage.team,
    )

    return team_result.output
```

### Team Stage Dispatch (TypeScript — pi-parallel-agents DAG)

```typescript
async function dispatchTeamStage(
  stage: StageConfig,
  issue: Issue,
  worktree: string,
  priorOutputs: Map<string, string>,
): Promise<string> {
  const teamConfig = {
    team: {
      objective: `Execute stage '${stage.ref}' for issue #${issue.number}`,
      members: Object.entries(stage.team).map(([role, cfg]) => ({
        role,
        agent: cfg.agent,
        model: `pi/${cfg.model_role}`,
      })),
      tasks: buildDagTasks(stage.team, priorOutputs),
    },
  };

  const result = await executeTeam(teamConfig, { cwd: worktree });
  return extractTeamOutput(result);
}
```

---

## 6. Workflow Loader

```python
import yaml
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class TeamMemberConfig:
    agent: str
    model_role: str = "default"

@dataclass
class StageConfig:
    ref: str
    agent: str
    type: str = "single"                   # "single" | "team"
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

def load_workflow(path: Path) -> WorkflowConfig:
    raw = yaml.safe_load(path.read_text())
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
            reviewers=s.get("reviewers", []),
            team=team,
        ))
    return WorkflowConfig(
        id=raw["id"],
        description=raw.get("description", ""),
        stages=stages,
    )

def load_all_workflows(workflows_dir: Path) -> dict[str, WorkflowConfig]:
    workflows = {}
    for path in sorted(workflows_dir.glob("*.yaml")):
        wf = load_workflow(path)
        workflows[wf.id] = wf
    return workflows
```

### Validation

```python
def validate_workflow(wf: WorkflowConfig, agents: dict[str, AgentDef]) -> list[str]:
    """Validate workflow references and structure."""
    errors = []
    refs = {s.ref for s in wf.stages}

    for stage in wf.stages:
        # Agent must exist
        if stage.agent not in agents and stage.type != "team":
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
```

---

## 7. How This Changes Both v3 Designs

### Python v3 Changes

| Component | Before | After |
|-----------|--------|-------|
| `orchestrator.py` | Hardcoded `plan_loop → dev_loop → review_loop` | Loads workflow YAML, calls `execute_workflow()` |
| `plan_loop.py` | Standalone function | Becomes a stage dispatcher (`dispatch_single_stage`) |
| `dev_loop.py` | Standalone function | Becomes a team stage dispatcher (`dispatch_team_stage`) |
| `multi_model.py` | Called directly | Becomes a multi-review stage dispatcher |
| New: `workflow_engine.py` | — | Core stage interpreter loop |
| New: `workflow_loader.py` | — | YAML loading + validation |
| New: `workflow_router.py` | — | Label-based workflow selection |
| `config.py` | No workflow config | Adds `workflow_selection` section |

### TypeScript v3 Changes

| Component | Before | After |
|-----------|--------|-------|
| `orchestrator.ts` | Hardcoded pipeline | Loads workflow YAML, calls `executeWorkflow()` |
| `plan-loop.ts` | Standalone function | Stage dispatcher for single stages |
| `dev-loop.ts` | DAG execution | Team stage dispatcher wrapping pi-parallel-agents |
| `multi-model.ts` | Called directly | Multi-review stage dispatcher |
| New: `workflow-engine.ts` | — | Core stage interpreter loop |
| New: `workflow-loader.ts` | — | YAML loading + validation |
| New: `workflow-router.ts` | — | Label-based workflow selection |
| `config.ts` | No workflow config | Adds `workflow_selection` section |

### Updated Project Structure

```
auto-dev-loop/
  workflows/                  # NEW — workflow definitions
    bug_fix.yaml
    feature.yaml
    documentation.yaml
    security_audit.yaml
    ops_change.yaml
  agents/                     # Unchanged — agent definitions
    architect.md
    tester.md
    developer.md
    reviewer.md
    ...
  src/
    workflow_engine.py        # NEW — stage interpreter
    workflow_loader.py        # NEW — YAML loading + validation
    workflow_router.py        # NEW — label-based routing
    orchestrator.py           # SIMPLIFIED — delegates to workflow engine
    plan_loop.py              # REFACTORED → stage dispatcher
    dev_loop.py               # REFACTORED → team stage dispatcher
    multi_model.py            # REFACTORED → multi-review stage dispatcher
    ...
  tests/
    test_workflow_engine.py   # NEW
    test_workflow_loader.py   # NEW
    test_workflow_router.py   # NEW
    ...
```

---

## 8. State Store Updates

New SQLite table to track workflow stage execution:

```sql
CREATE TABLE workflow_stages (
    id INTEGER PRIMARY KEY,
    issue_id INTEGER REFERENCES issues(id),
    workflow_id TEXT NOT NULL,
    stage_ref TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    -- 'pending', 'running', 'approved', 'rejected', 'vetoed', 'escalated'
    agent_output_summary TEXT,
    verdict TEXT,              -- 'approved', 'needs_revision', 'vetoed'
    feedback TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, workflow_id, stage_ref, iteration)
);
```

---

## 9. Why Not Story Loops

ClawControl uses "story loops" where the plan agent outputs sub-tasks and the engine iterates over each. We explicitly skip this because:

1. **GitHub sub-issues already solve task decomposition** — visible, trackable, independent PRs
2. **Each sub-issue gets its own workflow execution** — own worktree, own state, own PR
3. **Crash recovery is per-issue** — story loops create hidden state that's lost if daemon crashes
4. **Parallelism** — sub-issues can run concurrently across worktrees; story loops are sequential

The architect agent should decompose large work into sub-issues via `gh issue create`, not internal stories.

---

## 10. Attribution

- **Workflow YAML stage schema:** Inspired by [ClawControl](https://github.com/salexandr0s/clawcontrol) stage engine (`WorkflowStageConfig`)
- **Label-based routing:** Simplified from ClawControl's `WorkflowSelectionConfig`
- **Agent-as-markdown:** From pi-mono subagent extension (adopted in v3)
- **Model roles:** From oh-my-pi (adopted in v3)
- **DAG execution within team stages:** From pi-parallel-agents (TypeScript only)
- **Review parsing (APPROVED/NEEDS_REVISION):** From pi-parallel-agents (adopted in v3)
- **Story loops:** Evaluated and deliberately excluded in favor of GitHub sub-issues
