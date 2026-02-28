"""Label-based workflow selection."""

from __future__ import annotations

from .models import Issue, WorkflowSelectionConfig


def select_workflow(issue: Issue, config: WorkflowSelectionConfig) -> str:
    """Select workflow ID from issue labels. First match wins."""
    labels = set(issue.labels)

    # 1. Priority overrides
    if issue.priority and issue.priority in config.priority_overrides:
        overrides = config.priority_overrides[issue.priority]
        for label in labels:
            if label in overrides:
                return overrides[label]

    # 2. Label mapping
    for label in labels:
        if label in config.label_map:
            return config.label_map[label]

    # 3. Default
    return config.default
