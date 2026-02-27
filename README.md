# ADL Agent SDK

Autonomous development daemon that monitors GitHub Projects for issues, implements them via Claude Agent SDK Agent Teams, creates PRs, and iterates on multi-model review feedback — with Telegram-based human escalation.

## How It Works

```
GitHub Projects V2 (polling)
        │
        ▼
   Issue Detected ──► Workflow Router (label → YAML pipeline)
        │
        ▼
   ┌─ Plan Loop (SDK Sequential) ──────────────────┐
   │   architect → plan_reviewer → iterate          │
   └────────────────────────────────────────────────┘
        │
        ▼
   ┌─ Dev Loop (SDK Agent Teams) ───────────────────┐
   │   tester ⇄ developer (P2P messaging)           │
   │   multi-model review (Claude + Gemini + Codex)  │
   │   structured APPROVED / NEEDS_REVISION parsing  │
   └────────────────────────────────────────────────┘
        │
        ▼
   ┌─ Review Loop ──────────────────────────────────┐
   │   PR comments → fixer → push → wait → iterate  │
   └────────────────────────────────────────────────┘
        │
        ▼
   PR Created / Human Escalation (Telegram)
```

## Key Concepts

### Declarative Workflows

Different issue types route to different YAML-defined pipelines. No hardcoded `plan → dev → review` — the workflow engine interprets stage definitions at runtime.

```yaml
# workflows/bug_fix.yaml
id: bug_fix
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
      tester: { agent: tester, model_role: smol }
      developer: { agent: developer, model_role: default }
  - ref: multi_review
    agent: reviewer
    reviewers: [claude, gemini, codex]
    loopTarget: dev
    maxIterations: 3
```

Pre-built workflows: `bug_fix`, `feature`, `documentation`, `security_audit`, `ops_change`.

### Agent-as-Markdown

Agents are defined as markdown files with YAML frontmatter — declarative, version-controlled, model-role-based.

```markdown
---
name: tester
description: Runs test suite, reports structured failures
tools: [Bash, Read, Grep, Glob]
model_role: smol
max_turns: 30
---

You are a test runner agent. Run the project's test suite...
```

### Model Roles

Agents reference roles (`smol` / `default` / `slow`), not model names. Swap models in config, not in agent files.

```yaml
model_roles:
  smol: claude-haiku-4-5       # test runners, scouts
  default: claude-sonnet-4-5   # developers, architects
  slow: claude-opus-4-5        # reviewers, complex planning
```

### Multi-Model Review

Conservative consensus: any rejection = reject. Structured `APPROVED` / `NEEDS_REVISION` markers parsed deterministically from reviewer output. Claude reviews via Agent SDK, external models (Gemini, Codex) run as parallel Python subprocess peers.

### Human Escalation

File-based escalation (`needs_human.json`) routed to Telegram. Triggers on security veto (`canVeto` stages) or iteration cap exhaustion.

## Architecture

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Agent orchestration | Claude Agent SDK (`query()` + Agent Teams) |
| Plan loop | SDK Sequential — one `query()` per agent per cycle |
| Dev loop | SDK Agent Teams — parallel `Task` with P2P messaging |
| Multi-model review | `asyncio.gather()` — Claude + Gemini/Codex subprocess |
| Workflow engine | YAML stage interpreter with label-based routing |
| State | SQLite via aiosqlite |
| GitHub | `gh` CLI + GraphQL (Projects V2 polling, PR management) |
| Telegram | Raw `httpx` + long-polling |
| Git isolation | Worktrees per issue |
| Auth | Max subscription (OAuth) via Agent SDK |

## Project Structure

```
adl-agent-sdk/
  pyproject.toml
  src/
    auto_dev_loop/
      cli.py                 # Typer CLI entry point
      main.py                # asyncio event loop, daemon lifecycle
      orchestrator.py        # delegates to workflow engine
      workflow_engine.py     # stage interpreter loop
      workflow_loader.py     # YAML loading + validation
      workflow_router.py     # label-based workflow selection
      plan_loop.py           # SDK Sequential plan loop
      dev_loop.py            # SDK Agent Teams dev loop
      review_loop.py         # PR review loop with backoff
      multi_model.py         # multi-model review orchestration
      review_parser.py       # APPROVED/NEEDS_REVISION parsing
      agent_loader.py        # load agent defs from markdown
      model_roles.py         # role → model resolution
      hooks.py               # Bash safety hook
      poller.py              # GitHub Projects V2 polling
      telegram.py            # bot: send/receive/commands
      state.py               # SQLite operations
      worktrees.py           # git worktree create/cleanup
      config.py              # YAML config loader
      models.py              # dataclasses
  agents/                    # agent definitions (markdown + frontmatter)
  workflows/                 # workflow definitions (YAML)
  scripts/
    pr-comments.sh
  tests/
```

## Configuration

```yaml
# ~/.claude/auto-dev.yaml
version: 3

telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"

model_roles:
  smol: claude-haiku-4-5
  default: claude-sonnet-4-5
  slow: claude-opus-4-5

workflow_selection:
  default: feature
  label_map:
    bug: bug_fix
    feature: feature
    docs: documentation
    security: security_audit
    infrastructure: ops_change

defaults:
  poll_interval: 60
  max_concurrent: 1
  max_plan_iterations: 3
  max_dev_cycles: 5
  max_review_cycles: 5
  external_reviewers: [gemini]

repos:
  - path: /path/to/project
    project_number: 1
    columns:
      source: "Ready for Dev"
      in_progress: "In Progress"
      done: "Done"
```

## Dependencies

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

## Status

**Pre-implementation.** Phase 0 research and SDK validation complete. See `docs/` for design documents.

## License

MIT
