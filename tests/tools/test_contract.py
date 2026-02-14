from __future__ import annotations

import json

import pytest
import tiktoken

from nighthawk.json_renderer import RenderStyle
from nighthawk.tools import contracts
from nighthawk.tools.contracts import tool_result_failure_json_text, tool_result_success_json_text

_encoding = tiktoken.get_encoding("o200k_base")
_style: RenderStyle = "strict"
_budget = 2_000


def test_tool_result_success_is_valid_json_and_has_expected_shape() -> None:
    text = tool_result_success_json_text(value={"a": 1}, max_tokens=_budget, encoding=_encoding, style=_style)
    payload = json.loads(text)

    assert payload["value"] == {"a": 1}
    assert payload["error"] is None


def test_tool_result_failure_is_valid_json_and_has_expected_shape() -> None:
    text = tool_result_failure_json_text(kind="execution", message="nope", guidance="retry", max_tokens=_budget, encoding=_encoding, style=_style)
    payload = json.loads(text)

    assert payload["value"] is None

    error = payload["error"]
    assert error["kind"] == "execution"
    assert error["message"] == "nope"
    assert error["guidance"] == "retry"


def test_tool_result_success_never_raises_for_unknown_value() -> None:
    class NotJson:
        pass

    text = tool_result_success_json_text(value=NotJson(), max_tokens=_budget, encoding=_encoding, style=_style)
    payload = json.loads(text)

    assert payload["error"] is None
    assert payload["value"] == "<nonserializable>"


def test_tool_result_budget_allocation_is_90_10_and_uses_leftover(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_render_json_text(
        value: object,
        *,
        max_tokens: int,
        encoding: tiktoken.Encoding,
        style: RenderStyle,
    ) -> tuple[str, int]:
        calls.append(
            {
                "value": value,
                "max_tokens": max_tokens,
                "encoding": encoding,
                "style": style,
            }
        )
        if isinstance(value, dict) and "kind" in value:
            return "{}", 10
        return "{}", 0

    monkeypatch.setattr(contracts, "render_json_text", fake_render_json_text)

    _ = contracts.render_tool_result_json_text(
        value={"big": "payload"},
        error={"kind": "execution", "message": "nope", "guidance": None},
        max_tokens=100,
        encoding=_encoding,
        style=_style,
    )

    assert calls[0]["max_tokens"] == 90
    assert calls[1]["max_tokens"] == 90

    calls.clear()

    _ = contracts.render_tool_result_json_text(
        value={"big": "payload"},
        error=None,
        max_tokens=100,
        encoding=_encoding,
        style=_style,
    )

    assert calls == [
        {
            "value": {"big": "payload"},
            "max_tokens": 100,
            "encoding": _encoding,
            "style": _style,
        }
    ]
