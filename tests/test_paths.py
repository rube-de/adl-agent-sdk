"""Tests for path helpers."""

from auto_dev_loop._paths import ADL_HOME, repo_slug, repo_state_dir


def test_repo_slug_basic():
    assert repo_slug("rube-de", "adl-agent-sdk") == "rube-de-adl-agent-sdk"


def test_repo_slug_strips_whitespace():
    assert repo_slug("  owner ", " repo ") == "owner-repo"


def test_repo_slug_normalises_slashes():
    assert repo_slug("org/team", "my/repo") == "org-team-my-repo"


def test_repo_state_dir_returns_expected_path():
    result = repo_state_dir("rube-de-adl-agent-sdk")
    assert result == ADL_HOME / "repos" / "rube-de-adl-agent-sdk"


def test_repo_state_dir_is_under_adl_home():
    result = repo_state_dir("some-slug")
    assert str(result).startswith(str(ADL_HOME))
