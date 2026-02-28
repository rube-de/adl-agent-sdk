"""Tests for Bash safety hooks."""

import pytest

from auto_dev_loop.hooks import is_destructive_command, BLOCKED_PATTERNS


def test_blocks_rm_rf():
    assert is_destructive_command("rm -rf /") is True
    assert is_destructive_command("rm -rf .") is True


def test_blocks_git_push_force():
    assert is_destructive_command("git push --force") is True
    assert is_destructive_command("git push -f origin main") is True


def test_blocks_git_reset_hard():
    assert is_destructive_command("git reset --hard") is True


def test_blocks_drop_table():
    assert is_destructive_command("DROP TABLE users;") is True


def test_allows_safe_commands():
    assert is_destructive_command("python -m pytest") is False
    assert is_destructive_command("git status") is False
    assert is_destructive_command("ls -la") is False
    assert is_destructive_command("cat README.md") is False


def test_allows_git_push_normal():
    assert is_destructive_command("git push origin feature/my-branch") is False


def test_allows_rm_single_file():
    assert is_destructive_command("rm test.pyc") is False


def test_blocks_chmod_recursive():
    assert is_destructive_command("chmod -R 777 /") is True


def test_blocks_pkill():
    assert is_destructive_command("pkill -9 python") is True
    assert is_destructive_command("kill -9 1234") is True
