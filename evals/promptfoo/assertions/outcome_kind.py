"""Assert that the step outcome kind is valid and not an error."""

from __future__ import annotations

import json
from typing import Any


def get_assert(output: str, context: dict) -> dict[str, Any]:
    """Validate that outcome_kind is present, valid, and not an execution error.

    Config:
        expected_outcome_kind (str, optional): If specified, asserts the
            outcome kind matches exactly.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {"pass": False, "score": 0.0, "reason": "Output is not valid JSON"}

    outcome_kind = data.get("outcome_kind")
    valid_kinds = {"pass", "return", "raise", "break", "continue"}

    if outcome_kind == "error":
        error_message = data.get("error", "unknown error")
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Execution error: {error_message}",
        }

    if outcome_kind not in valid_kinds:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Invalid outcome_kind: {outcome_kind!r}",
        }

    config = context.get("config", {})
    expected_outcome_kind = config.get("expected_outcome_kind")

    if expected_outcome_kind is not None and outcome_kind != expected_outcome_kind:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"Expected outcome_kind {expected_outcome_kind!r}, got {outcome_kind!r}",
        }

    return {"pass": True, "score": 1.0, "reason": f"outcome_kind: {outcome_kind}"}
