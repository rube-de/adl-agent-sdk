from pathlib import Path

import pytest


@pytest.fixture
def tmp_agents_dir(tmp_path: Path) -> Path:
    """Create a temporary agents directory."""
    d = tmp_path / "agents"
    d.mkdir()
    return d


@pytest.fixture
def tmp_workflows_dir(tmp_path: Path) -> Path:
    """Create a temporary workflows directory."""
    d = tmp_path / "workflows"
    d.mkdir()
    return d


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> Path:
    """Return a path for a temporary config file."""
    return tmp_path / "auto-dev.yaml"
