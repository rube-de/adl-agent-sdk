# Agentic Coding: Tools, Orchestrators & Automation

> A landscape overview of the tools, patterns, and methodologies shaping AI-assisted software development in early 2026.

---

## 1. The Shift: From Writing Code to Managing Agents

The bottleneck in software production has moved. It is no longer "can AI write code?" but rather **inference time plus hard architectural thinking**. Most software is routine data-shuffling that agents handle in a single shot. The developer's role is evolving from typist to orchestrator — defining intent, providing context, and steering a fleet of autonomous agents.

Peter Steinberger captures this bluntly: he runs 3-8 projects simultaneously, commits directly to main, never reverts (asks the model to change it instead), and treats issue trackers as optional. His workflow is CLI-first, queue-based, and model-agnostic. The important skill is no longer syntax — it's **context engineering**.

Boris Cherny (creator of Claude Code at Anthropic) reveals the inside view: he runs **5 parallel local sessions plus 5-10 web sessions on claude.ai/code**, teleporting sessions between environments and initiating tasks from iOS throughout the day. His key insight — "give Claude a way to verify its work" improves results **2-3x**. This manifests as:

- **Plan mode first** (Shift+Tab twice), iterate on strategy before auto-accepting edits
- **Verification agents**: background subagents that test every change against the live product
- **Team CLAUDE.md as institutional knowledge**: checked into git, updated multiple times weekly with documented mistakes — each team's accumulated "muscle memory" for their agent
- **Hooks for the last 10%**: a `PostToolUse` hook auto-formats code, eliminating CI formatting failures
- **Permission allowlists** via `/permissions` instead of `dangerously-skip-permissions` — safer at scale
- **MCP servers** connecting agents to Slack, BigQuery, and Sentry for full operational context

Where Steinberger goes wide (cross-model, multi-project), Cherny goes deep (single model, maximum leverage through verification and team knowledge).

---

## 2. Coding Agent CLIs

The foundation layer. These are the terminal-native tools that execute code changes autonomously.

### Claude Code (Anthropic)

The dominant CLI agent for many developers. Terminal-first, supports hooks, subagents, skills, MCP servers, and experimental features like Agent Teams. Key capabilities:

- **Skills system**: Modular instruction packages loaded on-demand (`.claude/skills/`)
- **Hooks**: Lifecycle events (pre-tool, post-tool, stop) for custom automation
- **Subagents**: Spawn child agents for parallel or specialized work
- **CLAUDE.md context files**: Repository-level instructions for agent alignment

### Codex (OpenAI)

OpenAI's CLI agent, powered by GPT-5.2. Key differentiator: **reads before writing** — sometimes spending 10-15 minutes silently analyzing files before producing code. Slower per-task but dramatically higher first-shot success rate, eliminating "fixing the fix" loops. Features a queuing system for pipelining tasks.

### Jules Tools (Google)

Google's asynchronous coding agent. Every task runs in its own temporary remote VM, enabling massive parallelization without local resource constraints. The CLI (`@google/jules`) manages remote sessions, making it ideal for repo-scale tasks like dependency updates, test generation, and bug fixes. Work returns as PRs.

### Pi (Mario Zechner)

A minimal, "aggressively extensible" terminal coding harness. Pi provides primitives (terminal, context, tools) and deliberately omits heavy features like plan mode or sub-agents — those are handled via TypeScript extensions or external tools. Supports 15+ providers with mid-session model switching. Four modes: Interactive, Print/JSON, RPC, SDK.

---

## 3. The Claude Agent SDK

The Claude Agent SDK (Python and TypeScript) moves the autonomous capabilities of Claude Code from a CLI into a **programmable library**. It is the bridge between interactive terminal usage and production-grade agent infrastructure.

### Core Concept: Agent Loop as a Library

The fundamental shift: with the standard Anthropic Client SDK, developers manually implement the tool-use loop — call the API, check for `tool_use` stop reason, execute tools, feed results back. The Agent SDK eliminates this entirely. You call `query()` with a prompt and tool permissions; Claude autonomously sequences tool calls until the task is complete.

```python
# Client SDK: you implement the loop
response = client.messages.create(...)
while response.stop_reason == "tool_use":
    result = your_tool_executor(response.tool_use)
    response = client.messages.create(tool_result=result, **params)

# Agent SDK: Claude handles it autonomously
async for message in query(prompt="Fix the bug in auth.py"):
    print(message)
```

### Built-in Tools

The SDK ships with the same production-ready tools that power Claude Code:

- **Bash**: Shell command execution with timeout and sandbox controls
- **Read / Write / Edit**: Filesystem operations with line-level precision
- **Glob / Grep**: File search and content search
- **WebSearch / WebFetch**: Web browsing and content extraction
- **Task**: Spawn subagents for parallel or specialized subtasks

No need to implement tool executors — they're built in, battle-tested, and sandboxable.

### Hooks: Programmable Governance

Hooks intercept agent actions at key lifecycle points, enabling security enforcement, audit logging, and human-in-the-loop policies:

| Hook | Fires When | Use Cases |
|---|---|---|
| `PreToolUse` | Before a tool executes | Block dangerous commands, enforce allowlists |
| `PostToolUse` | After a tool completes | Audit logging, side-effect tracking |
| `SessionStart` | Session initializes | Inject project context, set environment |

Hooks support tool-specific matchers (e.g., only fire for `Bash` calls) and configurable timeouts:

```python
options = ClaudeAgentOptions(
    hooks={
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[validate_command], timeout=120),
            HookMatcher(hooks=[log_all_tools]),  # all tools
        ],
        "PostToolUse": [HookMatcher(hooks=[audit_logger])],
    }
)
```

### Subagents

Define specialized child agents with scoped tool access and focused system prompts:

```typescript
const options = {
  allowedTools: ["Read", "Glob", "Grep", "Task"],
  agents: {
    "code-reviewer": {
      description: "Expert code reviewer for quality and security.",
      prompt: "Analyze code quality and suggest improvements.",
      tools: ["Read", "Glob", "Grep"]
    }
  }
};
```

The parent agent delegates to subagents via the `Task` tool. Each subagent runs in its own context window, preventing context pollution in the parent session.

### MCP Server Integration

Native support for Model Context Protocol servers — connect agents to databases, browsers, APIs, or any external system:

```python
options = ClaudeAgentOptions(
    mcp_servers={
        "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        "postgres": {"command": "npx", "args": ["@modelcontextprotocol/server-postgres"]}
    }
)
```

### Session Management

Sessions persist conversational context across multiple queries. Capture a `session_id` from the initial response and use it to resume, fork, or continue conversations:

```python
# First query — capture session
async for message in query(prompt="Read the auth module", options=opts):
    if hasattr(message, "subtype") and message.subtype == "init":
        session_id = message.session_id

# Resume with full context
async for message in query(
    prompt="Now find all callers",
    options=ClaudeAgentOptions(resume=session_id)
):
    print(message.result)
```

This enables long-running workflows, persistent assistants, and multi-step pipelines that maintain state across invocations.

### What You Can Build

The SDK turns Claude Code into infrastructure:

- **CI/CD bots**: Autonomous agents that fix failing builds, update dependencies, or generate migration scripts
- **Custom coding assistants**: Domain-specific agents with tailored tools and context
- **Research pipelines**: Agents that search, analyze, and synthesize across codebases and the web
- **Review automation**: Continuous code review agents embedded in PR workflows
- **Agent orchestrators**: Build your own Gas Town or Antfarm using `query()` as the execution primitive

### Ecosystem: Projects Built on the SDK

- **Auto-Claude**: Desktop GUI framework running up to 12 parallel agent terminals with Kanban board, self-validating QA, and AI-powered merge conflict resolution
- **Nanoclaw**: Container-isolated agent environments using the SDK for security-sensitive workloads
- **Claude Review Loop**: Uses the SDK's hook system to enforce cross-model review before task completion

---

## 4. Orchestrators & Multi-Agent Systems

Single agents hit walls on complex tasks. Orchestrators coordinate multiple agents working in parallel or in structured pipelines.

### Gas Town (Steve Yegge)

Industrial-scale workspace manager for 20-30 concurrent agents. Built around a rich domain model:

- **The Mayor**: Primary AI coordinator, the human's main interface
- **Rigs**: Project containers wrapping git repositories
- **Polecats**: Worker agents spawned for specific tasks
- **Hooks**: Git worktree-based persistent storage that survives agent crashes
- **Beads**: Git-backed issue tracking system

Gas Town solves the **context loss on restart** problem through git-backed persistence. The MEOW workflow (Mayor-Enhanced Orchestration Workflow) decomposes features into Beads, assigns them to Polecats, and tracks progress via Convoys.

### dmux (Standard Agents)

A developer agent multiplexer using **tmux + git worktrees** for parallel development. Lightweight and interactive:

1. Press `n` to create a task
2. Type prompt, select agent (Claude Code, Codex, or OpenCode)
3. Agent launches in isolated worktree + tmux pane
4. Press `m` to auto-commit, merge back, and clean up

Key feature: **side-by-side launches** — run two different agents on the same prompt to compare results.

### Warp Oz

Cloud-native orchestration from Warp. Moves agent execution off the laptop into scalable Docker sandboxes on cloud VMs. Already responsible for **60% of Warp's internal PRs**. Features:

- Agent Session Sharing for real-time human steering
- Built-in scheduler for cron-based automation (e.g., "Fraud Bot" pattern — agents that run on schedules and proactively file corrective PRs)
- Cross-platform skill interop (Claude Code, Codex, OpenClaw)

### Antfarm

"One install. Zero infrastructure." Deterministic multi-agent workflows defined in YAML:

```
plan -> setup -> implement -> verify -> test -> PR -> review
```

Bundled workflows for feature development, security audits, and bug fixes. Each agent runs in a fresh session with clean context. Built on the Ralph Loop pattern. No Redis, no Kafka — just YAML + SQLite + cron.

### Claude Code Agent Teams (Experimental)

Anthropic's native multi-agent feature. Unlike subagents (hierarchical), teams enable **peer-to-peer communication**:

- Shared task list with autonomous claiming
- Inter-agent messaging for sharing findings or debating hypotheses
- Display modes: in-process (single terminal) or split-pane (tmux/iTerm2)
- Plan approval gates before teammates can proceed

Enables patterns like "architect + developer + devil's advocate" and adversarial debugging with 3-5 agents disproving each other's theories.

### CDT — Claude Dev Team (cc-skills)

A concrete implementation of Agent Teams as a Claude Code plugin. CDT defines a full development team with six collaborative roles:

| Role | Model | Responsibility |
|---|---|---|
| **Architect** | Opus | Component design, interfaces, data flow |
| **Product Manager** | Sonnet | Requirements validation, architecture challenges |
| **Developer** | Opus | Full implementation — no stubs, no TODOs |
| **Code-Tester** | Sonnet | Unit/integration tests, failure reporting |
| **QA-Tester** | Sonnet | UX testing (Storybook + browser) or integration/smoke tests |
| **Reviewer** | Opus | Quality, security, completeness, plan adherence |

Plus a **Researcher** subagent (Sonnet) for on-demand documentation lookups via Context7.

Four operating modes: `/cdt:plan-task` (design only), `/cdt:dev-task` (implement from plan), `/cdt:full-task` (plan → user approval → dev), and `/cdt:auto-task` (fully autonomous). Tasks execute wave-by-wave with quality gates between phases — Code-Tester iterates with Developer (max 3 cycles), then QA-Tester, then Reviewer.

Part of the [cc-skills](https://github.com/rube-de/cc-skills) plugin marketplace.

### CodexMonitor

A Tauri desktop GUI for orchestrating multiple Codex agents. Workspace-centric with built-in diff stats, branch management, and GitHub PR/Issue integration. Features a remote daemon mode accessible from iOS over Tailscale — bringing agent management to mobile.

### Takopi

Remote agent dispatch via **Telegram**. Takopi bridges messaging with local code execution — send a task from your phone, agents run on your machine, results stream back in real-time. Three workflow modes:

- **Assistant**: Ongoing chat with auto-continuation
- **Workspace**: Topic-based organization tied to projects and branches
- **Handoff**: Reply-to-continue with terminal-first control

Supports multiple runners (Claude Code, Codex, OpenCode, Pi), parallel worktrees, voice notes, file transfer, and task scheduling. Plugin API for extensibility. Solves the "away from my desk" problem — dispatch work remotely, pick up at the terminal later.

### Vibe Kanban

Multi-agent orchestration using a Kanban-style board interface. Agents claim and move cards through swim lanes, providing a visual project management layer over autonomous coding workflows.

---

## 5. Autonomous Loops & Automation Patterns

### The Ralph Technique (Geoffrey Huntley)

The original infinite implementation loop:

```bash
while :; do cat PROMPT.md | claude-code ; done
```

Philosophy: "Deterministically bad in an undeterministic world." Defects are identifiable and fixable through prompt tuning. The skill is crafting PROMPT.md — iterative refinement like tuning a guitar.

### Ralphy (Michael Shimeles)

Industrialized Ralph. An autonomous CLI that orchestrates engines (Claude Code, Codex, OpenCode) to implement PRDs until every checkbox is done. Two modes:

- **Single Task**: One prompt, one agent
- **PRD Mode**: Iterates through markdown checklists autonomously

Scaling features: git worktrees for isolation, "Sandbox Mode" (symlinked dependencies for speed), webhook notifications for Discord/Slack.

### Nightshift

"A Roomba for your codebase." Uses leftover daily AI budget to generate surprise PRs overnight — dead code removal, doc drift fixes, test gap filling, security patches. Budget-aware with configurable limits. Zero risk: everything lands as a PR; close what you don't want.

### The Oracle Pattern (Steinberger)

Agent-to-agent escalation. When a coding agent hits a hard problem, it writes context to markdown and escalates to a stronger model (e.g., GPT-5 Pro) which performs deep research across ~50 websites and returns an answer.

---

## 6. Quality Assurance: Cross-Model Review & TDD

The core insight: **an AI that wrote the code cannot objectively review it.** Cross-model review (Gemini reviewing Claude's code) catches what self-review misses.

### Orchestration Workflows (lebed2045)

A comprehensive set of **15 slash commands** (wf1-wf12, boris1-2, ddr, ddr2) implemented as `.claude/commands/` and `.claude/agents/` markdown files. The entire system is just markdown — no runtime dependencies, no build step. Install by pasting a prompt into Claude Code.

**The Five Sins** it prevents:

| Sin | Prevention |
|---|---|
| Claiming "done" without testing | `EXECUTION_BLOCK` proof required |
| No actual play-testing | `SMOKE_TEST` required |
| Self-fulfilling tests | `BASELINE_BLOCK` comparison |
| Fix-break cycle | `REGRESSION_DELTA` tracking |
| Ignoring warnings | `WARNING_COUNT` must not increase |

**Core architecture**: Workflows use **isolated coders** spawned via `claude -p` subprocess with ZERO planning context — they only see the approved spec and architecture files. This prevents context pollution where a coder implements rejected ideas from planning discussions. Reviewers are equally isolated: Gemini, Codex, and fresh Claude instances that know nothing about *why* the code was written.

**Anti-sycophancy oath** baked into every agent: *"I will NOT claim 'done' to end a frustrating session. I will show PROOF or admit I cannot verify."*

**Workflow spectrum** (15 commands):

| Workflow | Reviewers | Human Gates | Autonomy | Best For |
|---|---|---|---|---|
| `/wf1-gh` | Gemini | 1 | Low | Learning the system |
| `/wf3-gh` | Gemini + Claude | 1 | Medium | Production code |
| `/wf4-gc` | Gemini + Codex + Claude | 0 | Maximum | Autonomous features |
| `/wf6-gch` | 4 reviewers + retrospective | 1 | Medium | Security-critical code |
| `/wf7-gch` | Codex + Gemini (parallel) | 1 | Medium | Budget-conscious (75% fewer tokens) |
| `/wf8-gc` | Codex + Gemini | **0** | **Full** | Chores, auto-commit |
| `/wf9-gc` | Codex + Gemini + Agent Teams | 0 | High | Complex features with parallel TDD |
| `/wf11` | 5x Claude subprocesses | 0-1 | High | Anthropic-only (no MCP) |
| `/boris2` | 2x Opus | 0 | High | Boris Cherny's plan-iteration pattern |
| `/ddr2` | via wf8-gc | **0** | **Full** | Meta-orchestrator: auto-splits large tasks |

**DDR (Divide Delegate Reflect)** is the meta-orchestrator: reads PM cards from `.claude/pm/`, estimates LOC, and either delegates to a workflow directly (≤50 LOC) or recursively decomposes into 2-5 subtasks. On failure, it reflects, splits smaller, and recurses (max depth 2). Circuit breakers halt on: same failure 2x, max 10 commits, max 500 LOC.

**18 agent definitions** (coder-v1/v3/v4, planner-v1/v3/v4, intake-v1/v3/v4, fresh-reviewer-v1/v3/v4, code-simplifier, verify-app, coherence-cop, coverage-cop, simplicity-cop, plan-reviewer) — each a standalone markdown file with explicit tool permissions, model preferences, and behavioral constraints.

Source: [github.com/lebed2045/orchestration](https://github.com/lebed2045/orchestration)

### Codex Plan Critique (Adrian Leb)

A skill that invokes Codex as a "senior architect" during planning. Before the agent writes code, Codex reviews the plan for architectural consistency, edge cases, overengineering, and file integrity. Returns APPROVE or CONCERNS with actionable suggestions.

### Claude Review Loop (Hamel Husain)

Automated independent code review plugin. After Claude finishes implementation, a Stop hook intercepts exit and launches Codex for an independent audit covering code quality, test coverage, security (OWASP Top 10), and documentation alignment. The agent cannot exit until review feedback is addressed.

### Red/Green TDD (Simon Willison)

A simple prompt pattern with outsized impact: "Use red/green TDD."

- **Red phase**: Write tests first, confirm they fail
- **Green phase**: Implement until tests pass

All good models understand this shorthand. Protects against agents writing broken code, building unnecessary features, and creating tests that already pass without exercising new code.

### Council — Multi-Model Consensus Review (cc-skills)

A Claude Code plugin that orchestrates **5 external AI consultants** plus **2 specialized Claude subagents** for consensus-driven code review. The dual-layer architecture provides both model diversity and deep tool-assisted analysis:

**Layer 1 — External Consultants** (parallel, 120s timeout):
Gemini, Codex, Qwen, GLM-5, and Kimi K2.5 — each reviews the same diff independently via their respective CLIs.

**Layer 2 — Claude Subagents** (parallel, tool access):
`claude-deep-review` (Opus — security, bugs, performance: traces input paths, follows call chains) and `claude-codebase-context` (Sonnet — quality, compliance, git history, documentation).

**Layer 3 — Scoring** (noise reduction):
A Sonnet-powered scorer deduplicates findings across all 7 agents, reads actual code at referenced locations, scores each finding 0-100, and filters to >= 80. Built-in false positive rejection auto-drops pre-existing issues, linter-catchable problems, and pedantic nitpicks.

**Weighted synthesis** — not simple voting:

```
Weighted Score = Sum(Opinion * Expertise * Confidence) / Sum(Expertise * Confidence)
```

Review modes: broad review, security-focused, architecture, bugs, quality, plan validation, adversarial (advocates vs critics), consensus building, and quick triage (2-agent parallel check with auto-escalation).

Part of the [cc-skills](https://github.com/rube-de/cc-skills) plugin marketplace.

### Tessl Code Review Skills (Three-Layer Architecture)

Eight evaluated skills organized into composable layers:

| Layer | Purpose | Examples |
|---|---|---|
| **Reviewers** | Generate feedback | Sentry code-review (86%), secondsky (88%) |
| **Workflow** | Orchestrate process | superpowers requesting/receiving-code-review |
| **Plumbing** | Move data | github-pr-workflow (88%), coderabbit-fix-flow |

The `receiving-code-review` skill teaches agents to **push back** on suggestions with technical reasoning rather than blindly accepting every comment.

---

## 7. Context Engineering

### CLAUDE.md / AGENTS.md / CODEX.md

Repository-level instruction files that tell agents how to behave in a specific codebase. Used by Claude Code, Codex, and other tools respectively.

**Research caveat** (ETH Zurich, 2026): LLM-generated context files can *reduce* task success rates by ~3% and increase costs by 20-23% due to redundancy with existing documentation. Human-written files should be **minimal** — describe only repository-specific constraints and non-obvious tooling. Avoid auto-generated overviews for well-documented repos.

### The Context Development Lifecycle (CDLC)

Tessl's methodology treating context as a first-class engineering artifact with four stages:

1. **Generate**: Capture technical standards, resolve conflicting guidance
2. **Evaluate**: TDD for context — use evals to verify instructions work
3. **Distribute**: Version and publish context as packages (like npm for knowledge)
4. **Observe**: Monitor where agents ask clarifying questions or improvise incorrectly

Core thesis: infinite context windows won't solve reliability. Teams that engineer their context with versioning, testing, and monitoring will win.

### Skills as Package Management

The industry is converging on **skills** — modular instruction packages loaded on-demand:

- Token-efficient: only loaded when invoked, not perpetually in context
- Deterministic: bash scripts as first-class citizens
- Composable: mix reviewers, workflows, and plumbing from different authors
- Registries: Tessl Tile Registry provides pre-built documentation for thousands of libraries

The Karpathy Guidelines codify four principles as a reusable skill: Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution.

**Plugin Marketplaces** extend the skills model with full Claude Code plugin capabilities — hooks, agents, commands, and scripts alongside skill definitions. The [cc-skills](https://github.com/rube-de/cc-skills) marketplace bundles 8 plugins (council, cdt, project-manager, doppler, temporal, and more) installable via `claude plugin marketplace add`. Plugins install at user, project, or local scope — project-scoped plugins travel with the repo, making them available to cloud agents and teammates automatically.

---

## 8. Integration & Tooling Infrastructure

### Composio

10,000+ specialized tools and managed integrations for AI agents. Handles OAuth flows, triggers, and action execution across 90+ SaaS apps. Provides a **Managed MCP Gateway** — a single URL to access dozens of Model Context Protocol servers. Optimized JSON schemas improve tool-calling reliability by 30%.

### MCP (Model Context Protocol)

The emerging standard for tool integration. Servers expose capabilities (file operations, API calls, database queries) that agents consume through a unified protocol. Reduces integration drag and enables cross-platform agent interoperability.

---

## 9. Emerging Patterns & Philosophy

### CLI-First Architecture

Design for agents, not humans. Start with a CLI (agents can call and verify output directly), then layer UI on top. Use obvious directory structures and maintain `docs/` folders. Engineer codebases for agent navigation.

### The Managerial Inversion

The developer's role shifts from writing code to orchestrating multi-layered automated review and implementation pipelines. The human manages intent, context, and quality gates. Agents handle implementation.

### Proof Over Claims

Ban completion assertions without evidence. Require execution output, regression deltas, and test results before any agent can claim "done." Every claim must trace to verifiable output.

### Agent-Optimized Codebase Design

- Simple type systems that agents parse easily
- Markdown documentation over code comments
- Deterministic build systems
- Clear module boundaries
- Test suites as specification (red/green TDD)

---

## Tool Comparison Matrix

| Tool | Type | Execution | Isolation | Multi-Agent | Cost Model |
|---|---|---|---|---|---|
| **Claude Code** | CLI Agent | Local | Worktrees | Subagents + Teams | Subscription/API |
| **Codex** | CLI Agent | Local | Sandbox | Queue-based | Subscription/API |
| **Jules** | Cloud Agent | Remote VMs | VM-per-task | Parallel VMs | API |
| **Pi** | CLI Harness | Local | Extension-based | Via tmux | BYO Provider |
| **Gas Town** | Orchestrator | Local | Git Hooks | 20-30 agents | BYO Provider |
| **dmux** | Multiplexer | Local | Git Worktrees | Tmux panes | BYO Provider |
| **Warp Oz** | Cloud Platform | Cloud Docker | Container-per-task | Unlimited | Platform fee |
| **Antfarm** | Workflow Engine | Local | Fresh sessions | YAML pipelines | BYO Provider |
| **Ralphy** | Autonomous Loop | Local | Worktrees/Symlinks | PRD swarming | BYO Provider |
| **Takopi** | Remote Dispatch | Local (Telegram bridge) | Worktrees | Multi-runner | BYO Provider |
| **Nightshift** | Scheduled Runner | Local | Branch-per-task | Budget-aware | Leftover budget |

---

## Sources

All content synthesized from the `chad-knowledge` vault. Key documents:

- `ai-agents/2026-02-20-claude-agent-sdk.md` — Claude Agent SDK overview
- `claude-code/2026-02-17-auto-claude-framework.md` — Auto-Claude desktop framework
- `agentic-coding/moc-agentic-coding.md` — Master index of agentic coding notes
- `agentic-coding/2026-02-17-orchestration-workflows.md` — 10 workflows for cross-model review
- `agentic-coding/2026-02-17-dmux-parallel-agents.md` — dmux parallel development
- `agentic-coding/2026-02-17-gastown-orchestration.md` — Gas Town workspace manager
- `agentic-coding/2026-02-17-ralph-infinite-loop.md` — The Ralph Technique
- `agentic-coding/2026-02-17-shipping-at-inference-speed.md` — Steinberger's workflow
- `agentic-coding/2026-02-17-karpathy-coding-guidelines.md` — Karpathy Guidelines skill
- `agentic-coding/2026-02-17-tessl-code-review-skills.md` — 8 evaluated review skills
- `agentic-coding/2026-02-18-pi-coding-agent.md` — Pi coding harness
- `agentic-coding/2026-02-19-ralphy-autonomous-loop.md` — Ralphy autonomous CLI
- `agentic-coding/2026-02-21-codex-plan-critique-skill.md` — Codex Plan Critique
- `agentic-coding/2026-02-21-claude-review-loop.md` — Claude Review Loop
- `agentic-coding/2026-02-23-context-development-lifecycle-cdlc.md` — CDLC methodology
- `agentic-coding/2026-02-23-skills-registry-agent-package-manager.md` — Skills as packages
- `agentic-coding/2026-02-23-codexmonitor-tauri-orchestrator.md` — CodexMonitor GUI
- `agentic-coding/simon-willison/2026-02-19-red-green-tdd-pattern.md` — Red/Green TDD
- `ai-agents/2026-02-18-warp-oz-agent-orchestration.md` — Warp Oz platform
- `ai-agents/2026-02-19-composio-agent-tools.md` — Composio integrations
- `ai-agents/2026-02-20-claude-code-agent-teams.md` — Claude Code Agent Teams
- `ai-agents/2026-02-20-jules-google-coding-agent-cli.md` — Jules Tools CLI
- `openclaw/tools/2026-02-17-antfarm-teams.md` — Antfarm workflow engine
- `openclaw/tools/2026-02-17-nightshift-auto-pr.md` — Nightshift overnight PRs
- `research/2026-02-23-repository-level-context-files-effectiveness.md` — ETH Zurich context files study
- https://takopi.dev/ — Takopi: Telegram-bridged agent dispatch
- https://twitter-thread.com/t/2007179832300581177 — Boris Cherny's Claude Code workflow thread
- https://github.com/rube-de/cc-skills — cc-skills plugin marketplace (council, cdt, project-manager, etc.)
- https://github.com/lebed2045/orchestration — Orchestration Workflows: 15 commands, 18 agents, full source
