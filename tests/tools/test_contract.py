from __future__ import annotations

import json

from nighthawk.tools.contracts import tool_result_failure_json_text, tool_result_success_json_text


def test_tool_result_success_is_valid_json_and_has_expected_shape() -> None:
    text = tool_result_success_json_text({"a": 1})
    payload = json.loads(text)

    assert payload["status"] == "success"
    assert payload["value"] == {"a": 1}
    assert payload["error"] is None


def test_tool_result_failure_is_valid_json_and_has_expected_shape() -> None:
    text = tool_result_failure_json_text(kind="execution", message="nope", guidance="retry")
    payload = json.loads(text)

    assert payload["status"] == "failure"
    assert payload["value"] is None

    error = payload["error"]
    assert error["kind"] == "execution"
    assert error["message"] == "nope"
    assert error["guidance"] == "retry"


def test_tool_result_success_never_raises_for_unknown_value() -> None:
    class NotJson:
        pass

    text = tool_result_success_json_text(NotJson())
    payload = json.loads(text)

    assert payload["status"] == "success"
    assert payload["error"] is None
