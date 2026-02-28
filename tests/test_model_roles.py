"""Tests for model role resolution."""

import pytest

from auto_dev_loop.model_roles import resolve_model, ModelRoleError


ROLES = {
    "smol": "claude-haiku-4-5",
    "default": "claude-sonnet-4-5",
    "slow": "claude-opus-4-5",
}


def test_resolve_known_role():
    assert resolve_model("smol", ROLES) == "claude-haiku-4-5"
    assert resolve_model("default", ROLES) == "claude-sonnet-4-5"
    assert resolve_model("slow", ROLES) == "claude-opus-4-5"


def test_resolve_unknown_role_falls_back_to_default():
    assert resolve_model("unknown", ROLES) == "claude-sonnet-4-5"


def test_resolve_no_default_raises():
    roles_no_default = {"smol": "haiku"}
    with pytest.raises(ModelRoleError, match="default"):
        resolve_model("unknown", roles_no_default)


def test_resolve_empty_roles():
    with pytest.raises(ModelRoleError, match="default"):
        resolve_model("smol", {})
