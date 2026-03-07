"""Tests for adl add — repo onboarding."""

from auto_dev_loop.bundled import BUNDLED_AGENTS_DIR, BUNDLED_WORKFLOWS_DIR


def test_bundled_agents_dir_exists():
    assert BUNDLED_AGENTS_DIR.is_dir()
    agent_files = list(BUNDLED_AGENTS_DIR.glob("*.md"))
    assert len(agent_files) >= 1


def test_bundled_workflows_dir_exists():
    assert BUNDLED_WORKFLOWS_DIR.is_dir()
    workflow_files = list(BUNDLED_WORKFLOWS_DIR.glob("*.yaml"))
    assert len(workflow_files) >= 1
