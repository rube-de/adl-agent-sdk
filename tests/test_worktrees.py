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


@pytest.mark.asyncio
async def test_create_worktree(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    await create_worktree(git_repo, wt_path, branch="adl/issue-42")
    assert wt_path.exists()
    assert (wt_path / ".git").exists()


@pytest.mark.asyncio
async def test_create_worktree_already_exists(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    await create_worktree(git_repo, wt_path, branch="adl/issue-42")
    with pytest.raises(WorktreeError, match="already exists"):
        await create_worktree(git_repo, wt_path, branch="adl/issue-42-dup")


@pytest.mark.asyncio
async def test_delete_worktree(git_repo: Path, tmp_path: Path):
    wt_path = tmp_path / "worktrees" / "issue-42"
    await create_worktree(git_repo, wt_path, branch="adl/issue-42")
    await delete_worktree(git_repo, wt_path)
    assert not wt_path.exists()


@pytest.mark.asyncio
async def test_list_worktrees(git_repo: Path, tmp_path: Path):
    wt1 = tmp_path / "worktrees" / "issue-1"
    wt2 = tmp_path / "worktrees" / "issue-2"
    await create_worktree(git_repo, wt1, branch="adl/issue-1")
    await create_worktree(git_repo, wt2, branch="adl/issue-2")
    wts = await list_worktrees(git_repo)
    paths = [str(wt["path"]) for wt in wts]
    assert str(wt1) in paths
    assert str(wt2) in paths


@pytest.mark.asyncio
async def test_create_worktree_rejects_path_traversal(git_repo: Path, tmp_path: Path):
    """Defense-in-depth: even if branch sanitization fails, worktree creation
    must reject paths that resolve outside the parent directory."""
    safe_parent = tmp_path / "worktrees"
    safe_parent.mkdir()
    evil_path = safe_parent / ".." / "escaped"
    with pytest.raises(WorktreeError, match="outside"):
        await create_worktree(git_repo, evil_path, branch="adl/1-evil")
