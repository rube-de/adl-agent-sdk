"""Callback data encoding/decoding for inline keyboard buttons.

Telegram limits callback_data to 64 bytes.
Format: "adl:{action}:{issue_id}:{stage_ref}"
"""

from __future__ import annotations

ACTIONS = ("approve", "reject", "feedback")


def encode_callback(action: str, issue_id: int, stage_ref: str) -> str:
    if action not in ACTIONS:
        raise ValueError(f"Invalid action '{action}', must be one of {ACTIONS}")
    data = f"adl:{action}:{issue_id}:{stage_ref}"
    if len(data.encode()) > 64:
        raise ValueError(f"Callback data too long: {len(data.encode())} bytes (max 64)")
    return data


def decode_callback(data: str) -> tuple[str, int, str] | None:
    """Returns (action, issue_id, stage_ref) or None if not an ADL callback."""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "adl":
        return None
    action, issue_id_str, stage_ref = parts[1], parts[2], parts[3]
    if action not in ACTIONS:
        return None
    try:
        issue_id = int(issue_id_str)
    except ValueError:
        return None
    return action, issue_id, stage_ref
