# Phase 0 PoC Results

**Date:** 2026-02-26
**SDK:** claude-agent-sdk 0.1.41
**Python:** 3.12.8

---

## Summary

| Test | Result | Notes |
|------|--------|-------|
| A — Skill invocation | ✅ PASS | `setting_sources=["user"]` loads user skills; `Skill` tool callable |
| B — Agent Teams | ✅ PASS | Works via SDK; requires concurrent spawn + Bash polling + `max_turns=200` |
| C — Hook intercept | ❌ FAIL | Python SDK `HookMatcher` does not intercept `AskUserQuestion` |

---

## Test A: Skill invocation — PASS

**Tools called:** `Skill`, `Bash`

`query()` with `setting_sources=["user", "project"]` correctly loads user-scoped skills
from `~/.claude/skills/`. The agent called the `Skill` tool, loaded the `qmd` skill,
and executed `qmd status` via `Bash`.

**Validated:** SDK query with `setting_sources=["user"]` can invoke user skills.

---

## Test B: Agent Teams — PASS

**Tools available in SDK context:** `TeamCreate`, `TeamDelete`, `SendMessage`, `Task`

`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `ClaudeAgentOptions.env` enables team tools.
`permission_mode="bypassPermissions"` and `max_turns=200` are required for reliable operation.

### What works

| Capability | Status | Notes |
|------------|--------|-------|
| `TeamCreate` | ✅ | Creates team; name is auto-generated (e.g. `rosy-pondering-forest`) |
| `Task` with `team_name` | ✅ | Spawns concurrent team-scoped subagent |
| Parallel Task spawning | ✅ | Both Tasks must be spawned in the same orchestrator turn |
| Peer-to-peer `SendMessage` | ✅ | Works when both Tasks are running concurrently; sender and recipient must both be alive |
| Orchestrator → agent `SendMessage` | ✅ | Works for in-flight agents; use `shutdown_request` type for graceful exit |
| `TeamDelete` | ⚠️ | Requires retry loop; `rm -rf ~/.claude/teams/{name}` filesystem fallback needed |

### Required orchestration pattern

Tasks must be spawned in the **same orchestrator turn** (parallel). The orchestrator must
continue working via `Bash` polling after spawning — do not wait for UserMessages from Tasks.

```
1. TeamCreate
2. Task(alpha) + Task(beta) — same response turn
3. Bash: sleep N && ls output_dir/  — poll until output files appear
4. Read output files, synthesize results
5. SendMessage shutdown_request to each agent
6. TeamDelete (with retry + rm -rf fallback)
```

If the orchestrator waits passively after spawning (no Bash work), it receives a
`ResultMessage` before Task results arrive. Bash polling keeps the session alive.

### What does NOT work

| Capability | Status | Notes |
|------------|--------|-------|
| `Teammate` tool | ❌ | Not registered in SDK context — interactive Claude Code only |
| Sequential spawn + wait for UserMessage | ❌ | Orchestrator exits before tasks complete |
| `claude -p` as alternative | ❌ | Same `Task`-only limitations; no advantage over SDK |

### Subprocess hang

Agent Teams child processes keep the parent `claude` process alive after `ResultMessage`.
In test scripts: detect `ResultMessage`, evaluate result, call `os._exit()`.
In daemon code: handle via structured cleanup (shutdown_request → TeamDelete → exit).

---

## Test C: Hook intercept of AskUserQuestion — FAIL

The agent called `AskUserQuestion` but the Python SDK `HookMatcher` hook never fired.
The hook framework errored internally:

```
Error in hook callback hook_0: [internal Claude Code JS stream content]
error: Stream closed
```

`HookMatcher` in `ClaudeAgentOptions.hooks` does not intercept `AskUserQuestion`.
The `updatedInput`/`answers` injection mechanism is not viable.

**Fallback for Phase 1:** Bash `PreToolUse` hook via `settings.json` with a local HTTP
server or Unix socket bridge. The daemon listens for hook payloads and responds with
pre-filled answers.

---

## Impact on Phase 1 Architecture

| Capability | Status | Phase 1 approach |
|------------|--------|-----------------|
| Skill invocation via SDK | ✅ Confirmed | `setting_sources=["user"]` + `Skill` tool |
| Agent Teams (parallel Tasks) | ✅ Confirmed | Concurrent spawn + `max_turns=200` + Bash polling |
| P2P agent messaging | ✅ Confirmed | Concurrent Tasks can `SendMessage` each other directly |
| Orchestrator → agent `SendMessage` | ✅ Confirmed | Works; use `shutdown_request` for graceful shutdown |
| Hook intercept of `AskUserQuestion` | ❌ Not viable via Python SDK | Bash hook in `settings.json` + HTTP bridge |
| `TeamDelete` | ⚠️ Unreliable | Retry loop + `rm -rf ~/.claude/teams/{name}` fallback |
| `Teammate` (persistent agents) | ❌ Interactive-only | Not available in any automated context |
