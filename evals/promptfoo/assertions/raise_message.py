"""Assert that raise outcome fields match expected values."""

from __future__ import annotations

import json
from typing import Any


def get_assert(output: str, context: dict) -> dict[str, Any]:
    """Validate raise outcome message and error type.

    Config:
        expected_raise_message (str, optional): Exact raise message match.
        expected_error_type (str, optional): Exact error type match.
        expected_raise_message_contains (str, optional): Substring match
            against raise message.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {"pass": False, "score": 0.0, "reason": "Output is not valid JSON"}

    outcome_kind = data.get("outcome_kind")
    if outcome_kind != "raise":
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Expected outcome_kind 'raise', got {outcome_kind!r}",
        }

    config = context.get("config", {})
    raise_message = data.get("raise_message", "")
    raise_error_type = data.get("raise_error_type", "")

    # Check exact message match
    expected_raise_message = config.get("expected_raise_message")
    if expected_raise_message is not None and raise_message != expected_raise_message:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Expected raise_message {expected_raise_message!r}, got {raise_message!r}",
        }

    # Check error type match
    expected_error_type = config.get("expected_error_type")
    if expected_error_type is not None and raise_error_type != expected_error_type:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Expected raise_error_type {expected_error_type!r}, got {raise_error_type!r}",
        }

    # Check message contains
    expected_contains = config.get("expected_raise_message_contains")
    if expected_contains is not None and expected_contains not in raise_message:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Expected raise_message to contain {expected_contains!r}, got {raise_message!r}",
        }

    return {"pass": True, "score": 1.0, "reason": f"Raise outcome matches: {raise_message!r}"}
