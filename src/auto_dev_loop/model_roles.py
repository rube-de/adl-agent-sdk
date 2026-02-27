"""Role-based model resolution."""

from __future__ import annotations


class ModelRoleError(Exception):
    pass


def resolve_model(role: str, model_roles: dict[str, str]) -> str:
    """Resolve a model role to a concrete model name.

    Falls back to 'default' role if the requested role is not found.
    """
    if role in model_roles:
        return model_roles[role]
    if "default" in model_roles:
        return model_roles["default"]
    raise ModelRoleError(
        f"Role '{role}' not found and no 'default' role configured. "
        f"Available roles: {list(model_roles.keys())}"
    )
