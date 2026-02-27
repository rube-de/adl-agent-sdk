# Architecture Comparison: SDK Agent Teams vs CDT vs Sequential Loops

**Date:** 2026-02-26
**Based on:** Phase 0 PoC results + probe suite + kargarisaac.medium.com article validation

---

## Context

Three approaches exist for building the Auto Dev Loop daemon:

1. **SDK Agent Teams** — `query()` as orchestrator, parallel `Task` workers, Bash polling
2. **CDT Agent Teams** — interactive Claude Code with `Teammate` tool (persistent P2P agents)
3. **SDK Sequential Loops** — Python daemon, one `query()` call per agent per cycle, no Agent Teams

---

## Capability Matrix

| Capability | SDK Agent Teams | CDT (interactive) | SDK Sequential |
|------------|----------------|-------------------|----------------|
| Runs as unattended daemon | ✅ | ❌ terminal required | ✅ |
| P2P messaging (tester → developer) | ✅ concurrent Tasks | ✅ Teammate | ❌ Python relay |
| Parallel execution | ✅ same-turn spawn | ✅ | ❌ one call at a time |
| Persistent named agents | ❌ one-shot per spawn | ✅ | ❌ |
| Python controls loop logic | ✅ | ❌ orchestrator is Claude | ✅ |
| External reviewer (Gemini, Codex) | ✅ Python peers | ⚠️ Bash subcommands | ✅ Python peers |
| Crash resilience | ⚠️ session lost | ❌ session lost | ✅ Python serialises state |
| TeamDelete reliability | ⚠️ needs retry + rm -rf | ✅ | N/A |
| Quality gate hooks (TeammateIdle) | ❌ | ✅ | ❌ |
| Requires experimental env var | ✅ | ✅ | ❌ |
| Human can intervene mid-session | ❌ | ✅ | ❌ |

---

## Token Cost

**SDK Agent Teams:**
- N concurrent Tasks = N parallel billing sessions during execution
- Orchestrator context grows with Bash polling turns
- Each Task starts with isolated, minimal context (spawn prompt + CLAUDE.md only)

**CDT:**
- Same as SDK Agent Teams structurally: each Teammate has isolated context window
- Higher coordination overhead (mailbox, task list infrastructure)
- Requires human presence = interactive terminal session billing

**SDK Sequential:**
- 1 session active at a time
- Python controls exactly what each agent receives — no accumulation
- Lowest total token cost for sequential workflows

Official Claude Code docs: *"Agent teams use significantly more tokens than a single session.
Token usage scales with the number of active teammates."*

For a dev loop running 10+ iterations, SDK Sequential has lowest token cost.
SDK Agent Teams is worth the extra cost when P2P or parallel execution provides real value.

---

## The Persistent Agent Gap

The one genuine advantage CDT has over SDK Agent Teams: `Teammate` agents persist
indefinitely. They can receive `SendMessage` at any time, maintain conversational
history across multiple exchanges, and be reused across many cycles.

SDK `Task` agents are one-shot. Each spawn is a fresh context. For the dev loop this means:
- Each tester run = new Task spawn
- Each developer fix = new Task spawn
- No agent remembers previous cycles

In practice this is fine — each cycle should start fresh anyway. The developer agent
receiving "these 3 tests fail" doesn't benefit from remembering that last cycle had
5 failures. Fresh context avoids bias and context bloat.

The persistent agent model adds value for **open-ended creative work** (design debates,
hypothesis investigation) where an agent genuinely benefits from its own conversation
history. The dev loop is deterministic and cycle-based — persistence doesn't help.

---

## Plan Loop and Dev Loop Analysis

### Plan loop: Architect ↔ Reviewer

```
SDK Agent Teams approach:
  1. TeamCreate
  2. Task(architect) + Task(reviewer) — concurrent spawn
  3. Architect writes plan to file, SendMessage to reviewer: "plan ready"
  4. Reviewer reads plan file, sends verdict: "approved" or "feedback: ..."
  5. If feedback: spawn new Task(architect) with plan + feedback
  6. Repeat until approved

SDK Sequential approach:
  loop:
    plan = query(architect, prompt=spec + prior_feedback)
    verdict = query(reviewer, prompt=plan)
    if approved: break
    prior_feedback = extract_feedback(verdict)
```

For plan review, **SDK Sequential is simpler** — the cycle is inherently sequential
(reviewer can't review before architect writes). P2P adds no value here.

### Dev loop: Tester → Developer → Reviewer

```
SDK Agent Teams approach:
  1. TeamCreate
  2. Task(tester) + Task(developer) — concurrent spawn
  3. Tester runs tests, writes failures to file, SendMessage to developer: "errors ready"
  4. Developer reads error file, writes patches
  5. Tester re-runs tests (can happen concurrently with developer if independent modules)
  6. TeamDelete + repeat if still failing

SDK Sequential approach:
  loop:
    failures = query(tester, prompt=codebase)
    if no failures: break
    patches = query(developer, prompt=failures)
    apply(patches)
    verdict = query(reviewer, prompt=patches)  # optional
```

For dev loop, **SDK Agent Teams adds real value** — tester and developer can exchange
messages directly, and parallel execution means tester can run while developer
is working on a different module. The P2P path avoids routing every failure through
the orchestrator.

---

## External Reviewer Integration

Both SDK approaches (Agent Teams and Sequential) treat Gemini/Codex as Python peers:

```python
# After developer produces patches, review in parallel
claude_review, gemini_review = await asyncio.gather(
    query(claude_reviewer, prompt=patches),
    run(["gemini", "-p", f"Security review:\n{patches}"]),
)
synthesis = await query(synthesizer, prompt=f"Claude: {claude_review}\nGemini: {gemini_review}")
```

CDT can only invoke external reviewers via `Bash` tool calls inside Claude's session —
they become second-class tool results rather than parallel peers. This is a meaningful
structural difference for multi-model review pipelines.

---

## Recommendation

### For the Auto Dev Loop daemon: **SDK Agent Teams**

**Rationale:**

1. **All required primitives are confirmed working** (Phase 0):
   - Parallel Task spawning ✅
   - P2P messaging between concurrent Tasks ✅
   - `max_turns=200` + Bash polling keeps session alive ✅
   - TeamDelete (with retry + rm -rf fallback) ✅

2. **P2P eliminates orchestrator relay** — tester sends failures directly to developer,
   reducing orchestrator context growth and latency.

3. **Programmatic Python control** — loop logic (max iterations, abort conditions,
   retry strategy) lives in Python, not in Claude's prompt.

4. **External reviewer integration** — Gemini/Codex plug in as Python-level peers,
   not as Bash subcommands inside Claude's session.

5. **CDT is unavailable as a daemon** — `Teammate` requires interactive terminal.

### Use SDK Sequential for the **plan loop**

The plan loop (architect ↔ reviewer) is sequential by nature. SDK Sequential is simpler,
cheaper, and works without Agent Teams experimental features.

### Suggested Phase 1 loop structure

```
Python daemon
│
├── PlanLoop (SDK Sequential)
│   ├── query(architect) → plan draft
│   ├── query(reviewer)  → approved | feedback
│   └── repeat up to N iterations
│
└── DevLoop (SDK Agent Teams)
    ├── TeamCreate
    ├── Task(tester) + Task(developer) — concurrent
    │     tester:    run tests → write failures → SendMessage developer
    │     developer: read failures → write patches → SendMessage tester
    ├── Bash poll → read results
    ├── optional: query(gemini/codex reviewer) via Python subprocess
    ├── TeamDelete (retry + rm -rf fallback)
    └── repeat if tests still failing
```

### Known limitations to engineer around

| Issue | Mitigation |
|-------|-----------|
| `TeamDelete` unreliable | Retry loop (3–5 attempts) + `rm -rf ~/.claude/teams/{name}` fallback |
| Session lost on crash | Serialise loop state (iteration count, last failures) to disk before each spawn |
| Subprocess hang after ResultMessage | Detect ResultMessage + structured cleanup before exit |
| `AskUserQuestion` not interceptable via SDK | Bash `PreToolUse` hook in `settings.json` + local HTTP bridge |
| Tasks are one-shot | Acceptable — each dev cycle should start with fresh context |
