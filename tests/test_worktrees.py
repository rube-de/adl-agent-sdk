"""Tests for git worktree management."""

import subprocess
from pathlib import Path

import pytest

from auto_dev_loop.worktrees import (
    create_worktree,
    delete_worktree,
    list_worktrees,
    WorktreeError,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "-c", "commit.gpgsign=false",
         "commit", "--allow-empty", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


def test_create_worktree(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    create_worktree(git_repo, wt_path, branch="adl/issue-42")
    assert wt_path.exists()
    assert (wt_path / ".git").exists()


def test_create_worktree_already_exists(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    create_worktree(git_repo, wt_path, branch="adl/issue-42")
    with pytest.raises(WorktreeError, match="already exists"):
        create_worktree(git_repo, wt_path, branch="adl/issue-42-dup")


def test_delete_worktree(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    create_worktree(git_repo, wt_path, branch="adl/issue-42")
    delete_worktree(git_repo, wt_path)
    assert not wt_path.exists()


def test_list_worktrees(git_repo: Path, tmp_path: Path):
    wt1 = tmp_path / "worktrees" / "issue-1"
    wt2 = tmp_path / "worktrees" / "issue-2"
    create_worktree(git_repo, wt1, branch="adl/issue-1")
    create_worktree(git_repo, wt2, branch="adl/issue-2")
    wts = list_worktrees(git_repo)
    paths = [str(wt["path"]) for wt in wts]
    assert str(wt1) in paths
    assert str(wt2) in paths
