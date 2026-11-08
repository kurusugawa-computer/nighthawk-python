from __future__ import annotations

import ast
import json

import pytest
import tiktoken
from pydantic_ai.messages import BinaryContent

from nighthawk.adapters.pydantic_ai._rendering import project_discovery_value
from nighthawk.adapters.pydantic_ai.tools._rendering import (
    build_tool_handler_result_trace_text,
    render_tool_error_text,
    render_tool_handler_result_preview_text,
)
from nighthawk.adapters.pydantic_ai.tools.contracts import ToolResultObservation
from nighthawk.core.environment import StepEnvironment

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_encoding = tiktoken.get_encoding("o200k_base")
_budget = 2_000


def _preview(payload: object) -> str:
    return render_tool_handler_result_preview_text(
        tool_handler_result=ToolResultObservation(kind="tool_result", tool_outcome={"payload": payload, "error": None}),
        max_tokens=_budget,
        encoding=_encoding,
        max_depth=8,
    )


def test_rendered_success_text_is_the_preview() -> None:
    assert _preview("{'a': 1}") == "{'a': 1}"


def test_failure_preview_is_a_python_error_mapping() -> None:
    text = render_tool_handler_result_preview_text(
        tool_handler_result=ToolResultObservation(
            kind="tool_result",
            tool_outcome={"payload": None, "error": {"kind": "exception", "message": "nope", "guidance": "retry"}},
        ),
        max_tokens=_budget,
        encoding=_encoding,
        max_depth=8,
    )

    assert text == "error = {'kind': 'exception', 'message': 'nope', 'guidance': 'retry'}"


def test_control_feedback_is_not_shortened_by_the_budget() -> None:
    message = "Host policy refused this operation. " * 20
    text = render_tool_error_text(
        {"kind": "oversight", "message": message, "guidance": None, "status": "rejected"}, max_tokens=4, encoding=_encoding, max_depth=8
    )  # type: ignore[typeddict-unknown-key]

    assert ast.literal_eval(text.removeprefix("error = "))["message"] == message


def test_unrendered_payload_preview_never_raises_for_unknown_value() -> None:
    class NotJson:
        pass

    assert _preview(NotJson()) == "<opaque NotJson>"


def test_tool_result_hides_raw_payload_in_trace() -> None:
    tool_handler_result = ToolResultObservation(kind="tool_result", tool_outcome={"payload": "payload" * 100, "error": None})
    trace_text = build_tool_handler_result_trace_text(tool_handler_result=tool_handler_result, max_tokens=_budget, encoding=_encoding, max_depth=8)

    parsed_trace = json.loads(trace_text)
    assert parsed_trace == {"has_error": False, "error_kind": None, "preview_chars": 700}


@pytest.mark.parametrize(
    ("payload_factory", "expected_prefix"),
    [
        pytest.param(lambda binary: binary, "BinaryContent(data=<bytes; 8 bytes>, media_type='image/png'", id="scalar"),
        pytest.param(lambda binary: ["caption", binary], "['caption', BinaryContent(data=<bytes; 8 bytes>, media_type='image/png'", id="list"),
    ],
)
def test_tool_result_multimodal_preview_renders_binary_placeholder(payload_factory, expected_prefix: str) -> None:  # type: ignore[no-untyped-def]
    binary = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")

    assert _preview(payload_factory(binary)).startswith(expected_prefix)


@pytest.mark.parametrize("budget", [128, 512, 2_000])
def test_discovery_projection_preserves_usable_continuation_under_budget(budget: int) -> None:
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})
    projected = project_discovery_value(environment.discover(source="task"), max_tokens=budget, encoding=_encoding, max_depth=8)

    assert projected is not None and projected.token_count <= budget
    observed = ast.literal_eval(projected.text)
    namespace = environment._expression_namespace()
    next_page = eval(observed["continuation"], namespace, namespace)
    references = observed.get("references") or [entry["reference"] for entry in observed["names"]]
    for i, reference in enumerate(references):
        assert eval(reference, namespace, namespace) is environment.task_name_to_value[f"capability_{i:02}"]
    assert next_page["names"][0]["name"] == f"capability_{len(references):02}"


def test_insufficient_discovery_budget_returns_recovery_instead_of_a_broken_cursor() -> None:
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})
    projected = project_discovery_value(environment.discover(source="task"), max_tokens=64, encoding=_encoding, max_depth=8)

    assert projected is not None
    observed = ast.literal_eval(projected.text)
    assert "response budget" in observed["error"]
    assert "Python slices" in observed["recovery"]
    assert "next_cursor" not in observed and "continuation" not in observed
