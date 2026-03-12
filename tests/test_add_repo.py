"""Tests for adl add — repo onboarding."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
import yaml

from auto_dev_loop.add_repo import (
    AddRepoError,
    _remove_repo_config,
    append_repo_config,
    check_gh_available,
    detect_column_defaults,
    detect_github_remote,
    is_repo_configured,
    list_gh_projects,
    list_status_options,
    load_config_raw,
    run_add_wizard,
    scaffold_files,
)
from auto_dev_loop.bundled import BUNDLED_AGENTS_DIR, BUNDLED_WORKFLOWS_DIR
from auto_dev_loop.config import load_config, resolve_repo_config


def test_bundled_agents_dir_exists():
    assert BUNDLED_AGENTS_DIR.is_dir()
    agent_files = [
        entry
        for entry in BUNDLED_AGENTS_DIR.iterdir()
        if entry.is_file() and entry.name.endswith(".md")
    ]
    assert len(agent_files) >= 1


def test_bundled_workflows_dir_exists():
    assert BUNDLED_WORKFLOWS_DIR.is_dir()
    workflow_files = [
        entry
        for entry in BUNDLED_WORKFLOWS_DIR.iterdir()
        if entry.name.endswith(".yaml")
    ]
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


# --- GitHub detection helpers ---


def _mock_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class TestCheckGhAvailable:
    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_succeeds_when_gh_installed(self, mock_run):
        mock_run.return_value = _mock_run(stdout="gh version 2.40.0")
        check_gh_available()
        assert mock_run.call_count == 2  # --version + auth status

    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_raises_when_gh_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError("gh not found")
        with pytest.raises(AddRepoError, match="GitHub CLI"):
            check_gh_available()


class TestDetectGithubRemote:
    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_detects_owner_and_repo(self, mock_run, tmp_path: Path):
        mock_run.return_value = _mock_run(stdout="acme/my-app\n")
        owner, repo = detect_github_remote(tmp_path)
        assert owner == "acme"
        assert repo == "my-app"

    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_raises_on_failure(self, mock_run, tmp_path: Path):
        mock_run.return_value = _mock_run(returncode=1, stderr="not a git repo")
        with pytest.raises(AddRepoError, match="GitHub remote"):
            detect_github_remote(tmp_path)


class TestListGhProjects:
    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_returns_project_list(self, mock_run):
        projects = {
            "projects": [
                {"number": 1, "title": "Dev Board"},
                {"number": 3, "title": "Ops Board"},
            ],
            "totalCount": 2,
        }
        mock_run.return_value = _mock_run(stdout=json.dumps(projects))
        result = list_gh_projects("acme")
        assert len(result) == 2
        assert result[0]["number"] == 1
        assert result[1]["title"] == "Ops Board"

    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="auth required")
        with pytest.raises(AddRepoError, match="projects"):
            list_gh_projects("acme")

    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_returns_empty_list_when_no_projects(self, mock_run):
        mock_run.return_value = _mock_run(
            stdout=json.dumps({"projects": [], "totalCount": 0})
        )
        result = list_gh_projects("acme")
        assert result == []


class TestListStatusOptions:
    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_returns_status_options(self, mock_run):
        fields = {
            "fields": [
                {"name": "Title", "type": "ProjectV2Field"},
                {
                    "name": "Status",
                    "type": "ProjectV2SingleSelectField",
                    "options": [
                        {"name": "Todo"},
                        {"name": "In Progress"},
                        {"name": "Done"},
                    ],
                },
            ],
            "totalCount": 2,
        }
        mock_run.return_value = _mock_run(stdout=json.dumps(fields))
        result = list_status_options("acme", 1)
        assert result == ["Todo", "In Progress", "Done"]

    @patch("auto_dev_loop.add_repo.subprocess.run")
    def test_returns_empty_when_no_status_field(self, mock_run):
        fields = {
            "fields": [{"name": "Title", "type": "ProjectV2Field"}],
            "totalCount": 1,
        }
        mock_run.return_value = _mock_run(stdout=json.dumps(fields))
        result = list_status_options("acme", 1)
        assert result == []


# --- Column auto-detection ---


def test_detect_column_defaults_standard_names():
    options = ["Backlog", "Ready for Dev", "In Progress", "Done", "Archived"]
    result = detect_column_defaults(options)
    assert result == {
        "source": "Ready for Dev",
        "in_progress": "In Progress",
        "done": "Done",
    }


def test_detect_column_defaults_alternative_names():
    options = ["Todo", "Doing", "Complete"]
    result = detect_column_defaults(options)
    assert result == {
        "source": "Todo",
        "in_progress": "Doing",
        "done": "Complete",
    }


def test_detect_column_defaults_partial_match():
    options = ["Custom Source", "In Progress", "Done"]
    result = detect_column_defaults(options)
    assert result.get("source") is None
    assert result["in_progress"] == "In Progress"
    assert result["done"] == "Done"


def test_detect_column_defaults_no_match():
    options = ["Alpha", "Beta", "Gamma"]
    result = detect_column_defaults(options)
    assert result == {}


def test_detect_column_defaults_case_insensitive():
    options = ["ready for dev", "in progress", "done"]
    result = detect_column_defaults(options)
    assert result["source"] == "ready for dev"
    assert result["in_progress"] == "in progress"
    assert result["done"] == "done"


# --- Add wizard ---


def _setup_wizard_env(tmp_path, *, config_data=None):
    """Helper to create config file and a fake git repo dir."""
    cfg_path = tmp_path / "config.yaml"
    data = config_data or _minimal_config()
    _write_config(cfg_path, data)

    repo = tmp_path / "my-app"
    repo.mkdir(exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)

    return cfg_path, repo


class TestRunAddWizard:
    def test_fails_without_config(self, tmp_path: Path):
        cfg = tmp_path / "nonexistent.yaml"
        repo = tmp_path / "my-app"
        repo.mkdir()
        (repo / ".git").mkdir()
        with pytest.raises(typer.Exit) as exc_info:
            run_add_wizard(repo, cfg)
        assert exc_info.value.exit_code == 1

    def test_fails_without_git_dir(self, tmp_path: Path):
        cfg_path = tmp_path / "config.yaml"
        _write_config(cfg_path, _minimal_config())
        repo = tmp_path / "not-a-repo"
        repo.mkdir()
        with pytest.raises(typer.Exit) as exc_info:
            run_add_wizard(repo, cfg_path)
        assert exc_info.value.exit_code == 1

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    @patch("auto_dev_loop.add_repo.list_status_options")
    @patch("auto_dev_loop.add_repo.scaffold_files")
    def test_happy_path_single_project(
        self,
        mock_scaffold,
        mock_status,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
        monkeypatch,
    ):
        cfg_path, repo_path = _setup_wizard_env(tmp_path)

        mock_detect.return_value = ("acme", "my-app")
        mock_projects.return_value = [{"number": 1, "title": "Dev Board"}]
        mock_status.return_value = ["Ready for Dev", "In Progress", "Done"]
        mock_scaffold.return_value = ["developer.md"]

        # confirm: use detected columns? -> Yes
        monkeypatch.setattr(
            "auto_dev_loop.add_repo.typer.confirm", lambda *a, **kw: True
        )

        run_add_wizard(repo_path, cfg_path)

        result = load_config_raw(cfg_path)
        assert len(result["repos"]) == 1
        entry = result["repos"][0]
        assert entry == {
            "path": str(repo_path),
            "project_number": 1,
            "owner": "acme",
            "repo": "my-app",
            "columns": {
                "source": "Ready for Dev",
                "in_progress": "In Progress",
                "done": "Done",
            },
            "agents_dir": "./agents",
            "workflows_dir": "./workflows",
        }

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    def test_no_projects_exits_with_error(
        self,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
    ):
        cfg_path, repo_path = _setup_wizard_env(tmp_path)
        mock_detect.return_value = ("acme", "my-app")
        mock_projects.return_value = []

        with pytest.raises(typer.Exit) as exc_info:
            run_add_wizard(repo_path, cfg_path)
        assert exc_info.value.exit_code == 1

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    @patch("auto_dev_loop.add_repo.list_status_options")
    @patch("auto_dev_loop.add_repo.scaffold_files")
    def test_multiple_projects_prompts_selection(
        self,
        mock_scaffold,
        mock_status,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
        monkeypatch,
    ):
        cfg_path, repo_path = _setup_wizard_env(tmp_path)
        mock_detect.return_value = ("acme", "my-app")
        mock_projects.return_value = [
            {"number": 1, "title": "Dev Board"},
            {"number": 3, "title": "Ops Board"},
        ]
        mock_status.return_value = ["Todo", "In Progress", "Done"]
        mock_scaffold.return_value = []

        prompt_values = iter([1])  # select first project

        def fake_prompt(*args, **kwargs):
            return next(prompt_values)

        confirm_values = iter([True])  # accept column defaults

        def fake_confirm(*args, **kwargs):
            return next(confirm_values)

        monkeypatch.setattr("auto_dev_loop.add_repo.typer.prompt", fake_prompt)
        monkeypatch.setattr("auto_dev_loop.add_repo.typer.confirm", fake_confirm)

        run_add_wizard(repo_path, cfg_path)

        result = load_config_raw(cfg_path)
        assert result["repos"][0]["project_number"] == 1

    def test_already_configured_warns(self, tmp_path: Path, monkeypatch):
        repo_path = tmp_path / "my-app"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()
        cfg_path = tmp_path / "config.yaml"
        data = _minimal_config()
        data["repos"] = [{"path": str(repo_path), "project_number": 1}]
        _write_config(cfg_path, data)

        monkeypatch.setattr(
            "auto_dev_loop.add_repo.typer.confirm", lambda *a, **kw: False
        )

        with pytest.raises(typer.Exit) as exc_info:
            run_add_wizard(repo_path, cfg_path)
        assert exc_info.value.exit_code == 0

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    @patch("auto_dev_loop.add_repo.list_status_options")
    @patch("auto_dev_loop.add_repo.scaffold_files")
    @patch("auto_dev_loop.add_repo._remove_repo_config", wraps=_remove_repo_config)
    def test_reconfigure_replaces_existing_entry(
        self,
        mock_remove,
        mock_scaffold,
        mock_status,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
        monkeypatch,
    ):
        repo_path = tmp_path / "my-app"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()
        cfg_path = tmp_path / "config.yaml"
        data = _minimal_config()
        data["repos"] = [{"path": str(repo_path), "project_number": 1, "owner": "old"}]
        _write_config(cfg_path, data)

        mock_detect.return_value = ("acme", "my-app")
        mock_projects.return_value = [{"number": 5, "title": "New Board"}]
        mock_status.return_value = ["Todo", "In Progress", "Done"]
        mock_scaffold.return_value = []

        confirm_values = iter([True, True])  # Reconfigure? -> Yes, Use columns? -> Yes

        def fake_confirm(*args, **kwargs):
            return next(confirm_values)

        monkeypatch.setattr("auto_dev_loop.add_repo.typer.confirm", fake_confirm)

        run_add_wizard(repo_path, cfg_path)

        mock_remove.assert_called_once_with(cfg_path, repo_path.resolve())
        result = load_config_raw(cfg_path)
        assert len(result["repos"]) == 1
        entry = result["repos"][0]
        assert entry["project_number"] == 5
        assert entry["owner"] == "acme"
        assert entry["repo"] == "my-app"
        assert entry["agents_dir"] == "./agents"
        assert entry["workflows_dir"] == "./workflows"

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    @patch("auto_dev_loop.add_repo.list_status_options")
    @patch("auto_dev_loop.add_repo.scaffold_files")
    def test_no_status_field_prompts_manual_columns(
        self,
        mock_scaffold,
        mock_status,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
        monkeypatch,
    ):
        cfg_path, repo_path = _setup_wizard_env(tmp_path)

        mock_detect.return_value = ("acme", "my-app")
        mock_projects.return_value = [{"number": 2, "title": "Board"}]
        mock_status.return_value = []  # no Status field
        mock_scaffold.return_value = []

        prompt_values = iter(["Backlog", "Working", "Shipped"])

        def fake_prompt(*args, **kwargs):
            return next(prompt_values)

        monkeypatch.setattr("auto_dev_loop.add_repo.typer.prompt", fake_prompt)

        run_add_wizard(repo_path, cfg_path)

        result = load_config_raw(cfg_path)
        assert len(result["repos"]) == 1
        entry = result["repos"][0]
        assert entry["columns"] == {
            "source": "Backlog",
            "in_progress": "Working",
            "done": "Shipped",
        }
        assert entry["repo"] == "my-app"
        assert entry["agents_dir"] == "./agents"
        assert entry["workflows_dir"] == "./workflows"

    @patch("auto_dev_loop.add_repo.check_gh_available")
    @patch("auto_dev_loop.add_repo.detect_github_remote")
    @patch("auto_dev_loop.add_repo.list_gh_projects")
    @patch("auto_dev_loop.add_repo.list_status_options")
    @patch("auto_dev_loop.add_repo.scaffold_files")
    def test_wizard_entry_resolves_paths_via_config_roundtrip(
        self,
        mock_scaffold,
        mock_status,
        mock_projects,
        mock_detect,
        mock_gh_check,
        tmp_path: Path,
        monkeypatch,
    ):
        """Wizard-produced config entries resolve agents/workflows to absolute paths under each repo root."""
        cfg_path, repo_path = _setup_wizard_env(tmp_path)
        repo_path_2 = tmp_path / "my-app-2"
        repo_path_2.mkdir()
        (repo_path_2 / ".git").mkdir()

        mock_detect.side_effect = [("acme", "my-app"), ("acme", "my-app-2")]
        mock_projects.return_value = [{"number": 1, "title": "Board"}]
        mock_status.return_value = ["Ready for Dev", "In Progress", "Done"]
        mock_scaffold.return_value = []

        monkeypatch.setattr(
            "auto_dev_loop.add_repo.typer.confirm", lambda *a, **kw: True
        )

        run_add_wizard(repo_path, cfg_path)
        run_add_wizard(repo_path_2, cfg_path)

        config = load_config(cfg_path)
        assert len(config.repos) == 2
        resolved_1 = resolve_repo_config(config.repos[0], config)
        resolved_2 = resolve_repo_config(config.repos[1], config)
        assert resolved_1.defaults.agents_dir == str(repo_path / "agents")
        assert resolved_1.defaults.workflows_dir == str(repo_path / "workflows")
        assert resolved_2.defaults.agents_dir == str(repo_path_2 / "agents")
        assert resolved_2.defaults.workflows_dir == str(repo_path_2 / "workflows")
