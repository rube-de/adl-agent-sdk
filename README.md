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
  justfile
  src/
    auto_dev_loop/
      cli.py                 # Typer CLI entry point
      main.py                # asyncio daemon, polling loop
      config.py              # YAML config loader with env var expansion
      models.py              # core dataclasses (Config, Issue, AgentDef, etc.)
      orchestrator.py        # issue lifecycle state machine
      workflow_engine.py     # async stage interpreter loop
      workflow_loader.py     # YAML loading + validation
      workflow_router.py     # label-based workflow selection
      plan_loop.py           # sequential architect/reviewer iteration
      dev_loop.py            # agent team dev loop with review cycles
      review_loop.py         # PR review comment iteration with backoff
      multi_model.py         # parallel multi-model review (asyncio.gather)
      agent_query.py         # common SDK query wrapper (model resolution, hooks)
      agent_loader.py        # load agent defs from markdown + frontmatter
      model_roles.py         # role → model resolution
      review_parser.py       # APPROVED/NEEDS_REVISION parsing
      hooks.py               # Bash safety hook (destructive command blocking)
      poller.py              # GitHub Projects V2 polling (GraphQL)
      pr_status.py           # PR review/CI/merge status checking
      comments.py            # PR review comments extraction
      state.py               # SQLite state store (aiosqlite)
      worktrees.py           # git worktree create/delete/list
      issue_logging.py       # per-issue JSONL logging
      telegram/
        __init__.py          # TelegramBot facade
        models.py            # msgspec Structs (Update, Message, etc.)
        callbacks.py         # callback data encoding/decoding
        messages.py          # message builders (progress, escalation)
        bot_api.py           # raw HTTP Bot API client
        client.py            # rate-limited client wrapper
        outbox.py            # priority queue with edit coalescing
        poller.py            # long-polling update consumer
  agents/                    # agent definitions (markdown + frontmatter)
  workflows/                 # workflow definitions (YAML)
  tests/                     # 184 tests
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
    "msgspec",
    "pyyaml",
    "typer",
    "aiosqlite",
    "aiofiles",
]

[project.scripts]
adl = "auto_dev_loop.cli:app"
```

## Getting Started

```bash
# Install dependencies
uv sync

# Validate config
adl validate --config path/to/auto-dev.yaml

# Start daemon
adl run --config path/to/auto-dev.yaml

# Run tests
just test
```

## Status

**Implemented.** All 8 phases complete with 184 tests passing. See `docs/plans/` for phase-by-phase implementation plans.

## License

MIT
