"""Tests for adl add — repo onboarding."""

from pathlib import Path

from auto_dev_loop.add_repo import scaffold_files
from auto_dev_loop.bundled import BUNDLED_AGENTS_DIR, BUNDLED_WORKFLOWS_DIR


def test_bundled_agents_dir_exists():
    assert BUNDLED_AGENTS_DIR.is_dir()
    agent_files = list(BUNDLED_AGENTS_DIR.glob("*.md"))
    assert len(agent_files) >= 1


def test_bundled_workflows_dir_exists():
    assert BUNDLED_WORKFLOWS_DIR.is_dir()
    workflow_files = list(BUNDLED_WORKFLOWS_DIR.glob("*.yaml"))
    assert len(workflow_files) >= 1


def test_scaffold_copies_files_to_empty_dir(tmp_path: Path):
    target = tmp_path / "agents"
    copied = scaffold_files(BUNDLED_AGENTS_DIR, target)
    assert target.is_dir()
    assert len(copied) >= 1
    for name in copied:
        assert (target / name).exists()


def test_scaffold_skips_existing_files(tmp_path: Path):
    target = tmp_path / "agents"
    target.mkdir()
    existing = target / "developer.md"
    existing.write_text("custom content")

    copied = scaffold_files(BUNDLED_AGENTS_DIR, target)
    assert "developer.md" not in copied
    assert existing.read_text() == "custom content"
    assert len(list(target.iterdir())) > 1


def test_scaffold_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "agents"
    copied = scaffold_files(BUNDLED_AGENTS_DIR, target)
    assert target.is_dir()
    assert len(copied) >= 1
