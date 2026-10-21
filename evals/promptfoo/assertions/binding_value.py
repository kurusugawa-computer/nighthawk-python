"""Assert that binding values match expected values."""

from __future__ import annotations

import json
from typing import Any


def get_assert(output: str, context: dict) -> dict[str, Any]:
    """Compare actual bindings against expected values.

    Checks both the ``bindings`` dict (explicit write bindings) and
    ``step_locals`` (for in-place mutations) against expected values
    specified in ``context["config"]["expected_bindings"]``.

    Config:
        expected_bindings (dict): Mapping of binding name to expected value.
        check_step_locals (bool, optional): Also check step_locals for
            values not found in bindings. Defaults to False.
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {"pass": False, "score": 0.0, "reason": "Output is not valid JSON"}
    if not isinstance(data, dict):
        return {"pass": False, "score": 0.0, "reason": "Output must be an object"}

    config = context.get("config", {})
    expected_bindings: dict[str, Any] = config.get("expected_bindings", {})
    check_step_locals: bool = config.get("check_step_locals", False)

    if not expected_bindings:
        raise ValueError("expected_bindings must specify at least one binding")

    actual_bindings: dict[str, Any] = data.get("bindings", {})
    actual_step_locals: dict[str, Any] = data.get("step_locals", {})

    mismatches: list[str] = []
    for name, expected_value in expected_bindings.items():
        if name in actual_bindings:
            actual_value = actual_bindings[name]
        elif check_step_locals and name in actual_step_locals:
            actual_value = actual_step_locals[name]
        else:
            mismatches.append(f"{name}: missing binding; expected {expected_value!r}")
            continue

        if actual_value != expected_value:
            mismatches.append(f"{name}: expected {expected_value!r}, got {actual_value!r}")

    if mismatches:
        matched_count = len(expected_bindings) - len(mismatches)
        score = matched_count / len(expected_bindings)
        return {
            "pass": False,
            "score": score,
            "reason": "Binding mismatches: " + "; ".join(mismatches),
        }

    return {"pass": True, "score": 1.0, "reason": "All bindings match"}
