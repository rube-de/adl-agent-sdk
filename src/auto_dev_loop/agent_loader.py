"""Load agent definitions from markdown files with YAML frontmatter."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import AgentDef


class AgentLoadError(Exception):
    pass


def load_agent(path: Path) -> AgentDef:
    """Load a single agent definition from a markdown file."""
    text = path.read_text()

    # Split frontmatter from body
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AgentLoadError(f"{path.name}: no YAML frontmatter found (expected --- delimiters)")

    fm_text = parts[1]
    body = parts[2].strip()

    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise AgentLoadError(f"{path.name}: invalid YAML frontmatter: {e}")

    if not isinstance(fm, dict):
        raise AgentLoadError(f"{path.name}: frontmatter must be a YAML mapping")

    if "name" not in fm:
        raise AgentLoadError(f"{path.name}: missing required field 'name' in frontmatter")

    return AgentDef(
        name=fm["name"],
        description=fm.get("description", ""),
        system_prompt=body,
        tools=fm.get("tools", []),
        model_role=fm.get("model_role", "default"),
        max_turns=fm.get("max_turns", 50),
    )


def load_agents(agents_dir: Path) -> dict[str, AgentDef]:
    """Load all agent definitions from a directory."""
    agents: dict[str, AgentDef] = {}
    for path in sorted(agents_dir.glob("*.md")):
        agent = load_agent(path)
        agents[agent.name] = agent
    return agents
