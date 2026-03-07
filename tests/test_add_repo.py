"""Tests for adl add — repo onboarding."""

from pathlib import Path

import yaml

from auto_dev_loop.add_repo import (
    append_repo_config,
    is_repo_configured,
    load_config_raw,
    scaffold_files,
)
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


# --- Config manipulation helpers ---


def _write_config(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _minimal_config() -> dict:
    return {
        "version": 3,
        "telegram": {"bot_token": "fake", "chat_id": 123},
        "model_roles": {"default": "claude-sonnet-4-5"},
        "defaults": {},
        "repos": [],
    }


def test_load_config_raw(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    data = _minimal_config()
    _write_config(cfg_path, data)
    loaded = load_config_raw(cfg_path)
    assert loaded["version"] == 3
    assert loaded["repos"] == []


def test_is_repo_configured_false(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, _minimal_config())
    assert is_repo_configured(cfg_path, tmp_path / "my-app") is False


def test_is_repo_configured_true_by_path(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    repo_path = tmp_path / "my-app"
    repo_path.mkdir()
    data = _minimal_config()
    data["repos"] = [{"path": str(repo_path), "project_number": 1}]
    _write_config(cfg_path, data)
    assert is_repo_configured(cfg_path, repo_path) is True


def test_is_repo_configured_resolves_symlinks(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    repo_path = tmp_path / "my-app"
    repo_path.mkdir()
    link = tmp_path / "link-to-app"
    link.symlink_to(repo_path)
    data = _minimal_config()
    data["repos"] = [{"path": str(repo_path), "project_number": 1}]
    _write_config(cfg_path, data)
    assert is_repo_configured(cfg_path, link) is True


def test_append_repo_config_preserves_existing(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    data = _minimal_config()
    data["repos"] = [{"path": "/existing/repo", "project_number": 1, "owner": "alice"}]
    _write_config(cfg_path, data)

    new_entry = {
        "path": "/new/repo",
        "project_number": 5,
        "owner": "bob",
        "columns": {"source": "Todo", "in_progress": "Doing", "done": "Done"},
    }
    append_repo_config(cfg_path, new_entry)

    result = load_config_raw(cfg_path)
    assert len(result["repos"]) == 2
    assert result["repos"][0]["path"] == "/existing/repo"
    assert result["repos"][1]["path"] == "/new/repo"
    assert result["repos"][1]["columns"]["source"] == "Todo"
    assert result["version"] == 3
    assert result["telegram"]["chat_id"] == 123
