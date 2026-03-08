"""Bundled agent and workflow templates for scaffolding new repos."""

from importlib.resources import files

_BUNDLED_ROOT = files(__name__)

BUNDLED_AGENTS_DIR = _BUNDLED_ROOT / "agents"
BUNDLED_WORKFLOWS_DIR = _BUNDLED_ROOT / "workflows"
