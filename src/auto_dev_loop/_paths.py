"""Shared path constants for Auto Dev Loop."""

from pathlib import Path

ADL_HOME = Path.home() / ".adl"
ADL_CONFIG = ADL_HOME / "config.yaml"
ADL_STATE_DB = ADL_HOME / "state.db"
ADL_LOGS_DIR = ADL_HOME / "logs"
