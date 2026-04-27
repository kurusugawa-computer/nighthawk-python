from __future__ import annotations

import json

import pytest
import tiktoken

from nighthawk.json_renderer import JsonRendererStyle
from nighthawk.tools import contracts
from nighthawk.tools.contracts import (
    BoundaryFailureObservation,
    build_tool_handler_result_trace_text,
    build_tool_result_observation,
    render_tool_handler_result_preview_text,
    render_tool_result_json_text,
)

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_encoding = tiktoken.get_encoding("o200k_base")
_style: JsonRendererStyle = "strict"
_budget = 2_000


def test_tool_result_success_is_valid_json_and_has_expected_shape() -> None:
    text = render_tool_result_json_text(value={"a": 1}, error=None, max_tokens=_budget, encoding=_encoding, style=_style)
    payload = json.loads(text)

    assert payload["value"] == {"a": 1}
    assert payload["error"] is None


def test_tool_result_failure_is_valid_json_and_has_expected_shape() -> None:
    text = render_tool_result_json_text(
        value=None,
        error={"kind": "execution", "message": "nope", "guidance": "retry"},
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )
    payload = json.loads(text)

    assert payload["value"] is None

    error = payload["error"]
    assert error["kind"] == "execution"
    assert error["message"] == "nope"
    assert error["guidance"] == "retry"


def test_tool_result_success_never_raises_for_unknown_value() -> None:
    class NotJson:
        pass

    text = render_tool_result_json_text(value=NotJson(), error=None, max_tokens=_budget, encoding=_encoding, style=_style)
    payload = json.loads(text)

    assert payload["error"] is None
    assert payload["value"] == "<nonserializable>"


def test_build_tool_result_observation_hides_raw_payload_in_trace() -> None:
    tool_handler_result = build_tool_result_observation(
        tool_outcome={"payload": {"large": "payload" * 100}, "error": None},
    )
    preview_text = render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )
    trace_text = build_tool_handler_result_trace_text(
        tool_handler_result=tool_handler_result,
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )

    parsed_preview = json.loads(preview_text)
    parsed_trace = json.loads(trace_text)

    assert "value" in parsed_preview
    assert parsed_trace.get("has_error") is False
    assert "large" not in json.dumps(parsed_trace)


def test_boundary_failure_observation_uses_boundary_failure_trace_kind() -> None:
    tool_handler_result: BoundaryFailureObservation = {
        "kind": "boundary_failure",
        "tool_error": {
            "kind": "internal",
            "message": "wrapper boom",
            "guidance": "Retry.",
        },
    }
    preview_text = render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )
    trace_text = build_tool_handler_result_trace_text(
        tool_handler_result=tool_handler_result,
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )

    parsed_preview = json.loads(preview_text)
    parsed_trace = json.loads(trace_text)

    assert parsed_preview["error"]["message"] == "wrapper boom"
    assert parsed_trace["kind"] == "boundary_failure"
    assert isinstance(parsed_trace["preview_chars"], int)
    assert parsed_trace["preview_chars"] > 0


@pytest.mark.parametrize(
    ("payload_factory", "binary_index"),
    [
        pytest.param(lambda binary: binary, None, id="scalar"),
        pytest.param(lambda binary: ["caption", binary], 1, id="list"),
    ],
)
def test_build_tool_result_observation_multimodal_preview_renders_binary_placeholder(
    payload_factory,
    binary_index: int | None,
) -> None:  # type: ignore[no-untyped-def]
    from pydantic_ai.messages import BinaryContent

    binary = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")
    tool_handler_result = build_tool_result_observation(
        tool_outcome={"payload": payload_factory(binary), "error": None},
    )
    preview_text = render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=_budget,
        encoding=_encoding,
        style=_style,
    )

    parsed = json.loads(preview_text)
    assert parsed["error"] is None
    binary_preview = parsed["value"] if binary_index is None else parsed["value"][binary_index]
    assert binary_preview["kind"] == "binary"
    assert binary_preview["data"] == "<nonserializable>"
    if binary_index is not None:
        assert parsed["value"][0] == "caption"


def test_tool_result_budget_allocation_is_90_10_and_uses_leftover(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_render_json_text(
        value: object,
        *,
        max_tokens: int,
        encoding: tiktoken.Encoding,
        style: JsonRendererStyle,
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
