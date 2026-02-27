# Auto Dev Loop v3 — Python + Claude Agent SDK

> Autonomous development daemon: monitors GitHub Projects for issues, implements via SDK Agent Teams, creates PRs, iterates on review feedback with multi-model review — Telegram-based human escalation.

**Date:** 2026-02-27
**Status:** Draft v3
**Based on:** v2 design + pi ecosystem research (pi-mono, oh-my-pi, pi-parallel-agents patterns) + ClawControl workflow abstraction
**Parallel build:** This runs alongside the TypeScript/oh-my-pi implementation for empirical comparison.
**Workflow layer:** See `2026-02-27-workflow-abstraction-design.md` for the full workflow YAML schema, routing, and engine design.

---

## 0. Changes from v2

| Aspect | v2 | v3 |
|---|---|---|
| Agent prompts | Python dicts (`AGENT_PROMPTS`, `AGENT_TOOLS`) | **Markdown files with YAML frontmatter** (stolen from pi-mono subagent) |
| Model selection | Hardcoded model names | **Role-based mapping** (`smol`/`default`/`slow` — stolen from oh-my-pi) |
| Review parsing | Free-text verdict synthesis | **Structured APPROVED/NEEDS_REVISION parsing** (stolen from pi-parallel-agents) |
| Review history | Not tracked | **Per-cycle review history** with iteration tracking (stolen from pi-parallel-agents DAG) |
| Agent log/context split | Single log stream | **Full log + active context separation** (stolen from pi-mom) |
| External reviews | Single `run_external()` | **Parallel with per-model timeout and graceful degradation** |
| Project structure | Flat source | **Agent defs in `agents/` dir**, prompts in `prompts/` dir |

### New concepts stolen from pi ecosystem

| Source | Concept | How adopted |
|---|---|---|
| pi-mono subagent | Agent-as-markdown-file with YAML frontmatter | Agent definitions in `agents/*.md` with `name`, `tools`, `model_role`, `max_turns` |
| oh-my-pi | Model roles (`smol`/`default`/`slow`) | Config maps roles to models; agents reference roles not model names |
| pi-parallel-agents | Structured review parsing | `APPROVED` / `NEEDS_REVISION` markers in reviewer output, parsed deterministically |
| pi-parallel-agents | Review history tracking | `reviewHistory` array in state store: `{ iteration, workerOutput, reviewerOutput, approved }` |
| pi-parallel-agents | DAG-inspired task dependencies | Formalized dependency graph in dev loop (test → fix → verify → review) |
| pi-mom | Log + context separation | Full agent output → `log.jsonl`, active context → `context.jsonl` per issue |
| pi-mom | Channel-as-unit isolation | Validates: each issue = isolated worktree + agent session + log files |
| ClawControl | Workflow YAML stages | Different pipelines per issue type, defined in YAML with stages/conditions/loops |
| ClawControl | Workflow selection routing | GitHub label → workflow ID mapping for auto-routing |
| ClawControl | `canVeto` escalation | Security veto = escalate to human via Telegram |

---

## 1. Problem Statement

Unchanged from v2.

---

## 2. Architecture

### System Overview

```
Python Daemon (asyncio)

  Poller        Telegram     SQLite
  (gh CLI       Bot          State
  GraphQL)      (httpx)      Store

                Orchestration Loop

  detect --> claim --> PlanLoop --> DevLoop --> PR
                       (sequential) (Agent Teams)

  PlanLoop:
    query(architect) -> query(reviewer) -> iterate
    Agent prompts loaded from agents/*.md

  DevLoop:
    TeamCreate -> Task(tester)+Task(developer) -> Bash poll
    -> multi-model review (claude+gemini+codex) -> iterate
    Review parsing: APPROVED / NEEDS_REVISION markers
    Review history: tracked per-cycle in state store

  ReviewLoop:
    pr-comments.sh diff -> query(fixer) -> push -> wait
    -> check PR status -> iterate until APPROVED

  Human escalation: file-based -> Telegram -> new query()
```

### Technology Choices

| Component | Technology | Rationale |
|---|---|---|
| Language | Python 3.12+ | Agent SDK's primary language |
| Plan loop agents | SDK `query()` sequential | Inherently sequential |
| Dev loop agents | SDK Agent Teams (`TeamCreate` + parallel `Task`) | P2P tester-developer |
| Multi-model review | `asyncio.gather()` — Claude `query()` + Gemini/Codex subprocess | External reviewers as first-class peers |
| Agent definitions | Markdown files with YAML frontmatter | Declarative, version-controlled, model-role-based |
| Model selection | Role-based config (`smol`/`default`/`slow`) | Decouple agent defs from specific model names |
| Review parsing | Structured `APPROVED`/`NEEDS_REVISION` markers | Deterministic verdict extraction |
| Human escalation | File-based (`needs_human.json`) + Telegram routing | No hooks, no settings.json pollution |
| Bash safety | SDK `HookMatcher(matcher="Bash")` per-session | Blocks destructive commands, scoped to daemon |
| GitHub API | `gh` CLI + GraphQL | Projects V2 polling, PR management |
| Telegram | Raw `httpx` + long-polling | Lightweight |
| State | SQLite via aiosqlite | Issue tracking, review cycles, logs |
| Auth | Max subscription (OAuth) via Agent SDK | No API key needed |
| Git isolation | Worktrees per issue | Daemon stays on main |

---

## 3. Agent Definition Files (NEW)

Agents are defined as markdown files with YAML frontmatter, loaded at runtime.

### Format

```markdown
---
name: tester
description: Runs test suite, reports structured failures
tools: [Bash, Read, Grep, Glob]
model_role: smol
max_turns: 30
---

You are a test runner agent. Run the project's test suite and report
structured failures.

## Output Format

End your response with exactly one of:
- `TESTS_PASSING` — all tests pass
- `TESTS_FAILING` — followed by JSON block:

```json
{
  "total": 42,
  "passed": 38,
  "failed": 4,
  "failures": [
    { "test": "test_auth_login", "file": "tests/test_auth.py:42", "error": "AssertionError: ..." }
  ]
}
```
```

### Directory Structure

```
agents/
  architect.md         # Plan loop: reads issue, writes implementation plan
  plan_reviewer.md     # Plan loop: reviews plan, APPROVED or feedback
  tester.md            # Dev loop: runs tests, reports failures
  developer.md         # Dev loop: fixes code based on test failures + plan
  reviewer.md          # Dev loop: code review, APPROVED or NEEDS_REVISION
  pr_fixer.md          # Review loop: addresses PR review comments
  feedback_applier.md  # Review loop: applies human feedback from Telegram
  orchestrator.md      # Dev loop: Agent Teams orchestrator prompt
```

### Loading

```python
import yaml
from pathlib import Path
from dataclasses import dataclass

@dataclass
class AgentDef:
    name: str
    description: str
    system_prompt: str
    tools: list[str]
    model_role: str  # "smol", "default", "slow"
    max_turns: int

def load_agents(agents_dir: Path) -> dict[str, AgentDef]:
    agents = {}
    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text()
        # Split frontmatter from body
        _, fm_text, body = text.split("---", 2)
        fm = yaml.safe_load(fm_text)
        agents[fm["name"]] = AgentDef(
            name=fm["name"],
            description=fm.get("description", ""),
            system_prompt=body.strip(),
            tools=fm.get("tools", []),
            model_role=fm.get("model_role", "default"),
            max_turns=fm.get("max_turns", 50),
        )
    return agents
```

---

## 4. Model Roles (NEW)

Instead of hardcoding model names in agent definitions, agents reference roles.

### Configuration

```yaml
# ~/.claude/auto-dev.yaml
model_roles:
  smol: "claude-haiku-4-5"      # Fast, cheap: test runners, scouts
  default: "claude-sonnet-4-5"  # Standard: developers, architects
  slow: "claude-opus-4-5"       # Thorough: reviewers, complex planning

  # Future: per-role model overrides
  # smol: "gemini-2.5-flash"
  # developer: "codex-mini"
```

### Resolution

```python
def resolve_model(role: str, config: Config) -> str:
    return config.model_roles.get(role, config.model_roles["default"])
```

This decouples agent definitions from specific models. To switch the tester to a cheaper model, change the config — not the agent file.

---

## 5. Structured Review Parsing (NEW)

Reviewers must end their response with a structured verdict marker.

### Reviewer Agent Prompt (excerpt)

```markdown
## Verdict Format

End your response with exactly one of these markers on its own line:

APPROVED
— or —
NEEDS_REVISION

If NEEDS_REVISION, include a `## Feedback` section before the marker
with specific, actionable items.
```

### Parsing

```python
import re

@dataclass
class ReviewVerdict:
    approved: bool
    feedback: str | None

def parse_review_verdict(output: str) -> ReviewVerdict:
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]

    # Check last non-empty lines for markers
    for line in reversed(lines[-5:]):
        if line == "APPROVED":
            return ReviewVerdict(approved=True, feedback=None)
        if line == "NEEDS_REVISION":
            # Extract feedback section
            match = re.search(r"## Feedback\s*\n(.*?)(?=\nNEEDS_REVISION)", output, re.DOTALL)
            feedback = match.group(1).strip() if match else output
            return ReviewVerdict(approved=False, feedback=feedback)

    # No marker found — treat as needs revision (conservative)
    return ReviewVerdict(approved=False, feedback=output)
```

### Multi-Model Synthesis

```python
def synthesize_reviews(reviews: list[tuple[str, ReviewVerdict]]) -> ReviewVerdict:
    """Conservative: any rejection = reject. Feedback aggregated."""
    if all(r.approved for _, r in reviews):
        return ReviewVerdict(approved=True, feedback=None)

    feedback_parts = []
    for model, review in reviews:
        if not review.approved and review.feedback:
            feedback_parts.append(f"### {model}\n{review.feedback}")

    return ReviewVerdict(
        approved=False,
        feedback="\n\n".join(feedback_parts),
    )
```

---

## 6. Workflow Abstraction Layer (NEW)

**Full design:** See `2026-02-27-workflow-abstraction-design.md`

The orchestrator no longer hardcodes `plan_loop → dev_loop → review_loop`. Instead:

1. **Workflow YAML files** in `workflows/` define stage pipelines per issue type
2. **Workflow router** selects workflow based on GitHub issue labels
3. **Workflow engine** executes stages sequentially with iteration loops and escalation

```python
async def process_issue(issue: Issue, worktree: Path):
    # Route to workflow
    workflow_id = select_workflow(issue, config)
    workflow = load_workflow(config.workflows_dir / f"{workflow_id}.yaml")

    # Validate
    agents = load_agents(config.agents_dir)
    errors = validate_workflow(workflow, agents)
    if errors:
        raise WorkflowValidationError(errors)

    # Execute
    result = await execute_workflow(workflow, issue, worktree)

    if result.status == "completed":
        await create_pr(issue, worktree)
    elif result.status in ("vetoed", "escalated"):
        await notify_telegram(issue, result)
```

Pre-built workflows: `bug_fix`, `feature`, `documentation`, `security_audit`, `ops_change`.

### State Machine

Unchanged from v2, but stage transitions are now driven by workflow YAML rather than hardcoded. See v2 design doc section 3.

---

## 7. Component Design

### 7.1 Plan Loop (SDK Sequential)

Same as v2, but agent prompts loaded from markdown files and models resolved via roles.

```python
async def plan_loop(issue: Issue, worktree: Path) -> PlanResult:
    agents = load_agents(config.agents_dir)
    plan = None
    feedback = None

    for iteration in range(1, config.max_plan_iterations + 1):
        db.update_state(issue.id, "PLANNING")

        plan = await agent_query(
            agent_def=agents["architect"],
            prompt=build_architect_prompt(issue, plan, feedback),
            worktree=worktree,
        )

        claude_verdict, *external = await asyncio.gather(
            agent_query(
                agent_def=agents["plan_reviewer"],
                prompt=plan,
                worktree=worktree,
            ),
            *(external_review(model, plan) for model in config.plan_reviewers)
        )

        verdict = synthesize_plan_verdicts(claude_verdict, *external)

        if verdict.approved:
            db.update_state(issue.id, "PLAN_APPROVED")
            return PlanResult(plan=plan, iterations=iteration)

        feedback = verdict.feedback

    raise MaxIterationsError("plan", config.max_plan_iterations)
```

### 7.2 Common Agent Wrapper (Updated)

```python
async def agent_query(
    agent_def: AgentDef,
    prompt: str,
    worktree: Path,
    issue: Issue | None = None,
) -> str:
    model = resolve_model(agent_def.model_role, config)
    result_text = []

    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=agent_def.system_prompt,
            allowed_tools=agent_def.tools,
            cwd=str(worktree),
            permission_mode="bypassPermissions",
            max_turns=agent_def.max_turns,
            hooks={"PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[block_destructive]),
            ]},
        ),
    ):
        result_text.append(extract_text(msg))
        if issue:
            await write_log(issue, msg)

    return "\n".join(result_text)
```

### 7.3 Dev Loop (SDK Agent Teams)

Same as v2, but with structured review parsing and review history tracking.

```python
async def dev_loop(issue: Issue, plan: str, worktree: Path) -> DevResult:
    agents = load_agents(config.agents_dir)
    output_dir = worktree / ".adl-output"
    review_history: list[ReviewIteration] = []

    for cycle in range(1, config.max_dev_cycles + 1):
        db.update_state(issue.id, f"DEV_CYCLE_{cycle}")
        output_dir.mkdir(exist_ok=True)

        team_result = await run_agent_team(
            issue=issue, plan=plan, agents=agents,
            worktree=worktree, output_dir=output_dir, cycle=cycle,
        )

        if team_result.tests_passing:
            db.update_state(issue.id, "MULTI_MODEL_REVIEW")

            review = await multi_model_review(
                worktree=worktree, plan=plan, diff=team_result.diff,
                agents=agents,
            )

            # Track review history (stolen from pi-parallel-agents)
            review_history.append(ReviewIteration(
                cycle=cycle,
                worker_output=team_result.diff[:2000],
                reviewer_output=review.raw_output[:2000],
                approved=review.verdict.approved,
            ))
            db.store_review_history(issue.id, review_history)

            if review.verdict.approved:
                return DevResult(diff=team_result.diff, cycles=cycle)

            # Feed structured feedback back into plan
            plan = f"{plan}\n\n## Review feedback (cycle {cycle}):\n{review.verdict.feedback}"
            continue

    raise MaxIterationsError("dev", config.max_dev_cycles)
```

### 7.4 Multi-Model Review (Updated)

```python
async def multi_model_review(
    worktree: Path, plan: str, diff: str, agents: dict,
) -> MultiModelReviewResult:
    review_prompt = build_review_prompt(plan, diff)

    tasks = [
        agent_query(
            agent_def=agents["reviewer"],
            prompt=review_prompt,
            worktree=worktree,
        ),
    ]
    for model_cmd in config.external_reviewers:
        tasks.append(
            run_external_with_timeout(model_cmd, review_prompt, worktree)
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    reviews = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            continue  # Graceful degradation
        model_name = "claude" if i == 0 else config.external_reviewers[i - 1]
        verdict = parse_review_verdict(result)
        reviews.append((model_name, verdict))

    if not reviews:
        raise AllReviewersFailedError()

    return MultiModelReviewResult(
        verdict=synthesize_reviews(reviews),
        raw_output="\n---\n".join(r for _, (_, r) in zip(reviews, results) if not isinstance(r, Exception)),
        individual=reviews,
    )


async def run_external_with_timeout(cmd: str, prompt: str, worktree: Path) -> str:
    try:
        return await asyncio.wait_for(
            run_external(cmd, prompt, worktree),
            timeout=config.external_review_timeout,
        )
    except asyncio.TimeoutError:
        raise ExternalReviewTimeoutError(cmd)
```

### 7.5 PR Review Loop

Unchanged from v2. See v2 design doc section 4.5.

### 7.6 Bash Safety Hook

Unchanged from v2. See v2 design doc section 4.7.

### 7.7 Git Worktree Management

Unchanged from v2. See v2 design doc section 4.8.

### 7.8 Telegram Bot

Unchanged from v2. See v2 design doc section 4.9.

### 7.9 Observability (Updated)

Per-issue log files split into two streams (stolen from pi-mom):

```
~/.claude/auto-dev/logs/issues/{repo}-{N}/
  log.jsonl         # Full agent output — every message, tool call, result
  context.jsonl     # Active context — what matters for current cycle
  state.json        # Current state machine position + metadata
```

- `log.jsonl`: append-only, full history. Useful for debugging and auditing.
- `context.jsonl`: rebuilt each cycle with relevant subset. Useful for resuming.
- `state.json`: serialized state machine position, worktree path, iteration counts.

---

## 8. State Store (SQLite) — Updated

Added `review_iterations` table for review history tracking.

```sql
-- All tables from v2, plus:

CREATE TABLE review_iterations (
    id INTEGER PRIMARY KEY,
    issue_id INTEGER REFERENCES issues(id),
    dev_cycle INTEGER NOT NULL,
    iteration INTEGER NOT NULL,
    worker_output_summary TEXT,
    reviewer_output_summary TEXT,
    approved BOOLEAN,
    reviewer_models TEXT,  -- JSON array of model names that reviewed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, dev_cycle, iteration)
);
```

---

## 9. CLI Interface

Unchanged from v2.

---

## 10. Error Handling

Unchanged from v2. See v2 design doc section 7.

---

## 11. Security

Unchanged from v2. See v2 design doc section 8.

---

## 12. Project Structure (Updated)

```
auto-dev-loop/
  pyproject.toml
  src/
    auto_dev_loop/
      __init__.py
      cli.py                # Typer CLI entry point
      main.py               # asyncio event loop, daemon lifecycle
      orchestrator.py       # state machine, issue lifecycle
      plan_loop.py          # SDK Sequential plan loop
      dev_loop.py           # SDK Agent Teams dev loop
      review_loop.py        # PR review loop with backoff
      multi_model.py        # multi-model review orchestration
      review_parser.py      # APPROVED/NEEDS_REVISION parsing (NEW)
      agent_loader.py       # Load agent defs from markdown files (NEW)
      model_roles.py        # Role -> model resolution (NEW)
      workflow_engine.py    # Stage interpreter loop (NEW)
      workflow_loader.py    # YAML workflow loading + validation (NEW)
      workflow_router.py    # Label-based workflow selection (NEW)
      hooks.py              # Bash safety hook (SDK HookMatcher)
      poller.py             # GitHub Projects V2 polling
      telegram.py           # bot: send/receive/commands
      state.py              # SQLite operations (aiosqlite)
      comments.py           # pr-comments.sh wrapper + diff
      worktrees.py          # git worktree create/cleanup
      pr_status.py          # PR state/review/CI checking
      logging.py            # per-issue log capture (log+context split)
      config.py             # YAML config loader
      models.py             # dataclasses
  tests/
    test_orchestrator.py
    test_plan_loop.py
    test_dev_loop.py
    test_review_loop.py
    test_review_parser.py   # NEW: verdict parsing tests
    test_agent_loader.py    # NEW: markdown agent loading tests
    test_model_roles.py     # NEW: role resolution tests
    test_workflow_engine.py # NEW: stage interpreter tests
    test_workflow_loader.py # NEW: YAML loading tests
    test_workflow_router.py # NEW: label routing tests
    test_comments.py
    test_multi_model.py
    test_state.py
  agents/                   # Agent definitions (markdown + frontmatter) (NEW)
    architect.md
    plan_reviewer.md
    tester.md
    developer.md
    reviewer.md
    pr_fixer.md
    feedback_applier.md
    orchestrator.md
  scripts/
    pr-comments.sh          # vendored from cc-skills/dlc
```

---

## 13. Dependencies

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "claude-agent-sdk",
    "httpx",
    "pyyaml",
    "typer",
    "aiosqlite",
    "aiofiles",
]

[project.scripts]
adl = "auto_dev_loop.cli:app"
```

---

## 14. Configuration (Updated)

```yaml
version: 3

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"

model_roles:
  smol: "claude-haiku-4-5"
  default: "claude-sonnet-4-5"
  slow: "claude-opus-4-5"

defaults:
  agents_dir: "./agents"
  poll_interval: 60
  max_concurrent: 1
  max_plan_iterations: 3
  max_dev_cycles: 5
  max_review_cycles: 5
  review_backoff: [120, 300, 900, 1800, 3600]
  worker_timeout: 3600
  human_timeout: 3600
  external_review_timeout: 300
  circuit_breaker_failures: 3
  plan_reviewers: []
  external_reviewers: ["gemini"]

repos:
  - path: /Users/work/my-project
    project_number: 1
    columns:
      source: "Ready for Dev"
      in_progress: "In Progress"
      done: "Done"
```

---

## 15. Phase 0 Validation Status

Unchanged from v2. See v2 design doc section 11.

---

## 16. Open Questions

1. **Agent prompt engineering:** Same as v2 — P2P tester-developer prompts need design.

2. **Multi-model review synthesis:** Resolved — conservative (any rejection = reject), structured feedback aggregation.

3. **Token budget monitoring:** Should model roles include token budget limits? e.g., `smol: { model: "haiku", max_tokens: 50000 }`

4. **Review history replay:** If dev cycle N+1 starts with prior review feedback, should the developer agent also see the review history for cycle N? Could help or could bias.

5. **Agent hot-reload:** Should the daemon reload agent definitions from disk between cycles? Allows prompt tuning without restart.

---

## 17. Comparison Point for Bake-Off

This implementation will be compared against the TypeScript/oh-my-pi implementation on:

| Metric | How measured |
|--------|-------------|
| Success rate | % of test issues that reach PR_CREATED |
| Token cost | Total tokens per issue (all agents combined) |
| Wall time | Seconds from CLAIMED to PR_CREATED |
| Review quality | Manual assessment of generated code |
| Multi-model effectiveness | Did external reviewers catch bugs Claude missed? |
| Error recovery | How many failures recovered vs. required human intervention |
| Code complexity | LOC of daemon implementation |
| Maintainability | Ease of adding new agent roles, changing models, modifying workflows |
