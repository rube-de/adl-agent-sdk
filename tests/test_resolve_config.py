"""Tests for per-repo config resolution (merge logic)."""

import logging

import pytest

from auto_dev_loop.config import ConfigError, resolve_repo_config
from auto_dev_loop.models import (
    Config,
    Defaults,
    RepoConfig,
    ResolvedRepoConfig,
    TelegramConfig,
    WorkflowSelectionConfig,
)


def _make_global_config(**overrides) -> Config:
    base = dict(
        telegram=TelegramConfig(bot_token="tok", chat_id=1),
        model_roles={"smol": "haiku", "default": "sonnet", "slow": "opus"},
        repos=[],
        defaults=Defaults(
            agents_dir="./agents",
            workflows_dir="./workflows",
            max_dev_cycles=5,
            max_review_cycles=5,
            external_reviewers=["gemini"],
        ),
        workflow_selection=WorkflowSelectionConfig(
            default="feature",
            label_map={"bug": "bug_fix"},
        ),
    )
    base.update(overrides)
    return Config(**base)


def test_no_overrides_returns_global_values():
    gcfg = _make_global_config()
    repo = RepoConfig(path="/tmp/repo", project_number=1)
    resolved = resolve_repo_config(repo, gcfg)

    assert isinstance(resolved, ResolvedRepoConfig)
    assert resolved.defaults.agents_dir == "./agents"
    assert resolved.defaults.workflows_dir == "./workflows"
    assert resolved.defaults.max_dev_cycles == 5
    assert resolved.model_roles == {"smol": "haiku", "default": "sonnet", "slow": "opus"}
    assert resolved.workflow_selection.default == "feature"
    assert resolved.workflow_selection.label_map == {"bug": "bug_fix"}
    assert resolved.telegram == gcfg.telegram
    assert resolved.version == gcfg.version


def test_repo_agents_dir_overrides_global():
    gcfg = _make_global_config()
    repo = RepoConfig(path="/tmp/repo", project_number=1, agents_dir="./custom-agents")
    resolved = resolve_repo_config(repo, gcfg)
    # Relative path is rebased against repo.path
    assert resolved.defaults.agents_dir == "/tmp/repo/custom-agents"


def test_repo_workflows_dir_overrides_global():
    gcfg = _make_global_config()
    repo = RepoConfig(path="/tmp/repo", project_number=1, workflows_dir="./custom-wf")
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.workflows_dir == "/tmp/repo/custom-wf"


def test_repo_absolute_path_override_unchanged():
    """Absolute path overrides are not rebased."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo", project_number=1,
        agents_dir="/opt/shared-agents",
        workflows_dir="/opt/shared-workflows",
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.agents_dir == "/opt/shared-agents"
    assert resolved.defaults.workflows_dir == "/opt/shared-workflows"


def test_repo_defaults_relative_path_rebased():
    """Relative paths set via repo.defaults dict are also rebased."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo", project_number=1,
        defaults={"workflows_dir": "my-workflows"},
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.workflows_dir == "/tmp/repo/my-workflows"


def test_repo_defaults_shallow_merge():
    """Repo defaults override matching keys; global keys preserved."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        defaults={"max_dev_cycles": 3, "max_review_cycles": 10},
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.max_dev_cycles == 3
    assert resolved.defaults.max_review_cycles == 10
    # Global keys not overridden are preserved
    assert resolved.defaults.external_reviewers == ["gemini"]
    assert resolved.defaults.poll_interval == 60


def test_repo_workflow_selection_override():
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection={
            "default": "security_audit",
            "label_map": {"feature": "security_feature"},
        },
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.workflow_selection.default == "security_audit"
    # Repo label_map keys override global, global keys preserved
    assert resolved.workflow_selection.label_map == {
        "bug": "bug_fix",
        "feature": "security_feature",
    }


def test_repo_model_roles_merge():
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        model_roles={"slow": "claude-opus-4-5"},
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.model_roles == {
        "smol": "haiku",
        "default": "sonnet",
        "slow": "claude-opus-4-5",
    }


def test_repo_defaults_with_agents_dir_override():
    """Both repo.agents_dir and repo.defaults can coexist; agents_dir wins."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        agents_dir="./special-agents",
        defaults={"max_dev_cycles": 2, "agents_dir": "./from-defaults"},
    )
    resolved = resolve_repo_config(repo, gcfg)
    # Top-level agents_dir overrides the one set in defaults (rebased)
    assert resolved.defaults.agents_dir == "/tmp/repo/special-agents"
    assert resolved.defaults.max_dev_cycles == 2


def test_resolve_is_pure_does_not_mutate_inputs():
    gcfg = _make_global_config()
    original_mr = dict(gcfg.model_roles)
    original_lm = dict(gcfg.workflow_selection.label_map)

    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        model_roles={"slow": "overridden"},
        workflow_selection={"label_map": {"new": "wf"}},
    )
    resolve_repo_config(repo, gcfg)

    assert gcfg.model_roles == original_mr
    assert gcfg.workflow_selection.label_map == original_lm


def test_repo_workflow_selection_only_label_map():
    """Repo overrides only label_map, keeps global default workflow."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection={"label_map": {"docs": "documentation"}},
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.workflow_selection.default == "feature"  # global
    assert resolved.workflow_selection.label_map == {
        "bug": "bug_fix",
        "docs": "documentation",
    }


def test_repo_defaults_warns_on_unknown_keys(caplog):
    """Unknown keys in repo defaults dict log a warning."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        defaults={"max_dev_cycles": 2, "not_a_real_field": 999},
    )
    with caplog.at_level(logging.WARNING, logger="auto_dev_loop.config"):
        resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.max_dev_cycles == 2
    assert not hasattr(resolved.defaults, "not_a_real_field")
    assert "not_a_real_field" in caplog.text


def test_repo_priority_overrides_merge():
    """Per-repo priority_overrides are merged with global ones."""
    gcfg = _make_global_config(
        workflow_selection=WorkflowSelectionConfig(
            default="feature",
            label_map={"bug": "bug_fix"},
            priority_overrides={"P0": {"default": "hotfix"}},
        ),
    )
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection={
            "priority_overrides": {"P1": {"default": "standard"}},
        },
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.workflow_selection.priority_overrides == {
        "P0": {"default": "hotfix"},      # from global
        "P1": {"default": "standard"},     # from repo
    }


def test_repo_list_defaults_replaced_wholesale():
    """List-valued defaults are replaced, not appended."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        defaults={"external_reviewers": ["codex", "gemini"]},
    )
    resolved = resolve_repo_config(repo, gcfg)
    assert resolved.defaults.external_reviewers == ["codex", "gemini"]


def test_workflow_selection_not_a_dict_raises():
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection="bad",
    )
    with pytest.raises(ConfigError, match="must be a mapping"):
        resolve_repo_config(repo, gcfg)


def test_defaults_not_a_dict_raises():
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        defaults="bad",
    )
    with pytest.raises(ConfigError, match="must be a mapping"):
        resolve_repo_config(repo, gcfg)


def test_priority_overrides_inner_value_not_a_dict_raises():
    """Inner values of priority_overrides must be dicts."""
    gcfg = _make_global_config()
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection={
            "priority_overrides": {"P0": "bad_string"},
        },
    )
    with pytest.raises(ConfigError, match=r"priority_overrides\['P0'\].*must be a mapping"):
        resolve_repo_config(repo, gcfg)


def test_resolve_is_pure_priority_overrides_inner_dicts():
    """Inner dicts of priority_overrides are deep-copied — mutation doesn't leak."""
    gcfg = _make_global_config(
        workflow_selection=WorkflowSelectionConfig(
            default="feature",
            label_map={"bug": "bug_fix"},
            priority_overrides={"P0": {"default": "hotfix"}},
        ),
    )
    repo = RepoConfig(
        path="/tmp/repo",
        project_number=1,
        workflow_selection={
            "priority_overrides": {"P1": {"default": "standard"}},
        },
    )
    resolved = resolve_repo_config(repo, gcfg)

    # Mutate the resolved inner dict
    resolved.workflow_selection.priority_overrides["P0"]["default"] = "MUTATED"

    # Global config must be unaffected
    assert gcfg.workflow_selection.priority_overrides["P0"]["default"] == "hotfix"


def test_resolve_no_override_deep_copies_priority_overrides():
    """Even without repo overrides, inner dicts of priority_overrides are independent."""
    gcfg = _make_global_config(
        workflow_selection=WorkflowSelectionConfig(
            default="feature",
            label_map={},
            priority_overrides={"P0": {"default": "hotfix"}},
        ),
    )
    repo = RepoConfig(path="/tmp/repo", project_number=1)
    resolved = resolve_repo_config(repo, gcfg)

    resolved.workflow_selection.priority_overrides["P0"]["default"] = "MUTATED"
    assert gcfg.workflow_selection.priority_overrides["P0"]["default"] == "hotfix"
