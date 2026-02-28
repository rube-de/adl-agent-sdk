"""Tests for agent definition loader."""

from pathlib import Path

import pytest

from auto_dev_loop.agent_loader import load_agent, load_agents, AgentLoadError
from auto_dev_loop.models import AgentDef


VALID_AGENT_MD = """\
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
- `TESTS_FAILING` — followed by JSON block
"""

MINIMAL_AGENT_MD = """\
---
name: scout
description: Quick codebase scan
tools: [Read, Glob]
---

You scan the codebase for relevant files.
"""

MISSING_NAME_MD = """\
---
description: Missing name field
tools: [Bash]
---

Prompt body.
"""

NO_FRONTMATTER_MD = """\
Just a plain markdown file with no YAML frontmatter.
"""


def test_load_agent_full(tmp_agents_dir: Path):
    (tmp_agents_dir / "tester.md").write_text(VALID_AGENT_MD)
    agent = load_agent(tmp_agents_dir / "tester.md")
    assert agent.name == "tester"
    assert agent.description == "Runs test suite, reports structured failures"
    assert agent.tools == ["Bash", "Read", "Grep", "Glob"]
    assert agent.model_role == "smol"
    assert agent.max_turns == 30
    assert "test runner agent" in agent.system_prompt
    assert "## Output Format" in agent.system_prompt


def test_load_agent_defaults(tmp_agents_dir: Path):
    (tmp_agents_dir / "scout.md").write_text(MINIMAL_AGENT_MD)
    agent = load_agent(tmp_agents_dir / "scout.md")
    assert agent.model_role == "default"
    assert agent.max_turns == 50


def test_load_agent_missing_name(tmp_agents_dir: Path):
    (tmp_agents_dir / "bad.md").write_text(MISSING_NAME_MD)
    with pytest.raises(AgentLoadError, match="name"):
        load_agent(tmp_agents_dir / "bad.md")


def test_load_agent_no_frontmatter(tmp_agents_dir: Path):
    (tmp_agents_dir / "plain.md").write_text(NO_FRONTMATTER_MD)
    with pytest.raises(AgentLoadError, match="frontmatter"):
        load_agent(tmp_agents_dir / "plain.md")


def test_load_agents_directory(tmp_agents_dir: Path):
    (tmp_agents_dir / "tester.md").write_text(VALID_AGENT_MD)
    (tmp_agents_dir / "scout.md").write_text(MINIMAL_AGENT_MD)
    agents = load_agents(tmp_agents_dir)
    assert "tester" in agents
    assert "scout" in agents
    assert len(agents) == 2
    assert isinstance(agents["tester"], AgentDef)


def test_load_agents_empty_dir(tmp_agents_dir: Path):
    agents = load_agents(tmp_agents_dir)
    assert agents == {}


def test_load_agents_skips_non_md(tmp_agents_dir: Path):
    (tmp_agents_dir / "tester.md").write_text(VALID_AGENT_MD)
    (tmp_agents_dir / "README.txt").write_text("Not an agent file")
    agents = load_agents(tmp_agents_dir)
    assert len(agents) == 1


def test_load_real_agents():
    """Verify all agent definition files in agents/ are valid."""
    agents_dir = Path(__file__).parent.parent / "agents"
    if not agents_dir.exists() or not list(agents_dir.glob("*.md")):
        pytest.skip("agents/ directory not populated yet")
    agents = load_agents(agents_dir)
    expected = {
        "architect", "plan_reviewer", "tester", "developer",
        "reviewer", "pr_fixer", "feedback_applier", "orchestrator",
        "researcher", "security_reviewer",
    }
    assert set(agents.keys()) == expected
    for name, agent in agents.items():
        assert agent.system_prompt, f"{name} has empty system prompt"
        assert agent.tools, f"{name} has no tools"
