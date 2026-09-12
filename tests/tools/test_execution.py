from __future__ import annotations

from collections import namedtuple
from contextlib import nullcontext
from typing import Any

import anyio
import pytest
from pydantic_ai import Agent
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    ModelRequest,
    ToolReturnPart,
    tool_return_ta,
)
from pydantic_ai.messages import (
    TextContent as PydanticTextContent,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.contracts import (
    ToolBoundaryError,
    ToolOutcome,
)
from nighthawk.tools.execution import ToolResultWrapperToolset, _normalize_tool_success
from tests.support.executors import StubExecutor
from tests.support.tools import _new_run_context, _new_step_context

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _call_wrapped_tool(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
    inherit_run: bool = False,
) -> object:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> object:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with nullcontext() if inherit_run else nh.run(StubExecutor()), set_current_run_context(run_context):
            return await wrapped_toolset.call_tool(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def _call_wrapped_tool_outcome(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
    inherit_run: bool = False,
) -> ToolOutcome:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> ToolOutcome:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with nullcontext() if inherit_run else nh.run(StubExecutor()), set_current_run_context(run_context):
            return await wrapped_toolset.call_tool_outcome(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def test_wrapper_returns_success_payload_with_placeholder_for_unknown_value() -> None:
    class NotJson:
        pass

    def test_unknown_return() -> object:
        return NotJson()

    result = _call_wrapped_tool(name="test_unknown_return", function=test_unknown_return)
    assert result == "<nonserializable>"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n", "<nonserializable>"),
        (bytearray(b"\x89PNG\r\n\x1a\n"), "<nonserializable>"),
        (["caption", b"\x89PNG\r\n\x1a\n"], ["caption", "<nonserializable>"]),
        (("caption", b"\x89PNG\r\n\x1a\n"), ["caption", "<nonserializable>"]),
    ],
)
def test_wrapper_returns_success_payload_with_placeholder_for_raw_bytes(payload: object, expected: object) -> None:
    def test_bytes_return() -> object:
        return payload

    result = _call_wrapped_tool(name="test_bytes_return", function=test_bytes_return)
    assert result == expected


def test_wrapper_call_tool_outcome_propagates_application_exception() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _call_wrapped_tool_outcome(name="test_raises", function=test_raises)


def test_wrapper_call_tool_outcome_normalizes_bare_text_content_to_string() -> None:
    def test_text_content_return() -> object:
        return PydanticTextContent(content="hello")

    tool_outcome = _call_wrapped_tool_outcome(name="test_text_content_return", function=test_text_content_return)

    assert tool_outcome["error"] is None
    assert tool_outcome["payload"] == "hello"


def test_wrapper_call_tool_outcome_normalizes_text_content_in_multimodal_payload() -> None:
    def test_mixed_return() -> list[object]:
        return [
            PydanticTextContent(content="before"),
            BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
            PydanticTextContent(content="after"),
        ]

    tool_outcome = _call_wrapped_tool_outcome(name="test_mixed_return", function=test_mixed_return)

    assert tool_outcome["error"] is None
    payload = tool_outcome["payload"]
    assert isinstance(payload, list)
    assert payload[0] == "before"
    assert isinstance(payload[1], BinaryContent)
    assert payload[2] == "after"


def test_wrapper_call_tool_outcome_does_not_treat_namedtuple_as_top_level_multimodal_sequence() -> None:
    NamedImageReport = namedtuple("NamedImageReport", ["caption", "image"])

    def test_namedtuple_return() -> object:
        return NamedImageReport(
            caption="before",
            image=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
        )

    tool_outcome = _call_wrapped_tool_outcome(name="test_namedtuple_return", function=test_namedtuple_return)

    assert tool_outcome["error"] is None
    payload = tool_outcome["payload"]
    assert isinstance(payload, list)
    assert payload[0] == "before"
    assert isinstance(payload[1], dict)
    assert payload[1]["kind"] == "binary"
    assert all(not isinstance(item, BinaryContent) for item in payload)


def test_wrapper_call_tool_propagates_application_exception() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _call_wrapped_tool(name="test_raises", function=test_raises)


def test_wrapper_call_tool_outcome_propagates_oversight_rejection() -> None:
    tool_call_count = 0

    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        _ = run_context
        tool_call_count += 1
        return 1

    def reject_tool_call(tool_call: nh.runtime.OperationRequest) -> nh.oversight.Reject:
        assert tool_call.tool_name == "test_denied_tool"
        return nh.oversight.Reject("human rejected tool")

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_operation=reject_tool_call)),
        pytest.raises(nh.oversight.OversightRejectedError, match="human rejected tool"),
    ):
        _call_wrapped_tool_outcome(name="test_denied_tool", function=test_denied_tool, inherit_run=True)
    assert tool_call_count == 0


def test_wrapper_call_tool_propagates_oversight_rejection() -> None:
    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return 1

    def reject_tool_call(tool_call: nh.runtime.OperationRequest) -> nh.oversight.Reject:
        assert tool_call.tool_name == "test_denied_tool"
        return nh.oversight.Reject("human rejected tool")

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_operation=reject_tool_call)),
        pytest.raises(nh.oversight.OversightRejectedError, match="human rejected tool"),
    ):
        _call_wrapped_tool(name="test_denied_tool", function=test_denied_tool, inherit_run=True)


def test_wrapper_preserves_top_level_binary_content_as_file() -> None:
    def test_image_return() -> object:  # type: ignore[no-untyped-def]
        return BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")

    result = _call_wrapped_tool(name="test_image_return", function=test_image_return)
    tool_return_part = ToolReturnPart(tool_name="test_image_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"


def test_wrapper_preserves_list_binary_content_as_file() -> None:
    def test_mixed_return() -> list[object]:  # type: ignore[no-untyped-def]
        return ["caption", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")]

    result = _call_wrapped_tool(name="test_mixed_return", function=test_mixed_return)
    tool_return_part = ToolReturnPart(tool_name="test_mixed_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"
    dumped = tool_return_ta.dump_python(tool_return_part.content, mode="json")
    assert dumped[0] == "caption"


def test_wrapper_preserves_tuple_binary_content_as_file() -> None:
    def test_tuple_return() -> tuple[str, object]:
        return ("caption", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))

    result = _call_wrapped_tool(name="test_tuple_return", function=test_tuple_return)
    assert isinstance(result, list)
    tool_return_part = ToolReturnPart(tool_name="test_tuple_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "image/png"
    dumped = tool_return_ta.dump_python(tool_return_part.content, mode="json")
    assert dumped[0] == "caption"


def test_wrapper_preserves_top_level_audio_url_as_file() -> None:
    def test_audio_return() -> object:  # type: ignore[no-untyped-def]
        return AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")

    result = _call_wrapped_tool(name="test_audio_return", function=test_audio_return)
    tool_return_part = ToolReturnPart(tool_name="test_audio_return", content=result)
    assert tool_return_part.files
    assert tool_return_part.files[0].media_type == "audio/mpeg"


def test_wrapper_does_not_promote_nested_binary_content_to_file() -> None:
    def test_nested_binary() -> dict[str, object]:  # type: ignore[no-untyped-def]
        return {
            "value": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "error": None,
        }

    result = _call_wrapped_tool(name="test_nested_binary", function=test_nested_binary)
    tool_return_part = ToolReturnPart(tool_name="test_nested_binary", content=result)
    assert tool_return_part.files == []
    dumped = tool_return_ta.dump_python(tool_return_part.content, mode="json")
    assert dumped["value"]["kind"] == "binary"


def test_wrapper_preserves_none_return_as_success_without_error() -> None:
    def test_none_return() -> None:  # type: ignore[no-untyped-def]
        return None

    tool_return_part = ToolReturnPart(
        tool_name="test_none_return",
        content=_call_wrapped_tool(name="test_none_return", function=test_none_return),
    )
    assert tool_return_part.content is None
    assert tool_return_part.files == []


def test_wrapper_call_tool_shim_preserves_error_shaped_success_payload() -> None:
    def test_error_shaped_success() -> dict[str, str]:
        return {
            "kind": "user_requested",
            "message": "Operation completed with status",
            "guidance": "Next step is to review",
        }

    result = _call_wrapped_tool(name="test_error_shaped_success", function=test_error_shaped_success)
    assert isinstance(result, dict)
    assert result["kind"] == "user_requested"
    assert result["message"] == "Operation completed with status"
    assert result["guidance"] == "Next step is to review"


def test_provider_tool_loop_propagates_application_failure() -> None:
    tool_call_count = 0

    def test_failing_tool() -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        tool_call_count += 1
        raise ToolBoundaryError(
            kind="execution",
            message="tool crashed",
            guidance="Retry with different arguments.",
        )

    step_context = _new_step_context()
    wrapped_toolset = ToolResultWrapperToolset(FunctionToolset([test_failing_tool]))

    agent = Agent(
        model=TestModel(call_tools="all", custom_output_text="done"),
        deps_type=StepContext,
        output_type=str,
    )

    async def run_agent() -> tuple[object, list[ModelRequest]]:
        result = await agent.run("hello", deps=step_context, toolsets=[wrapped_toolset])
        model_request_list = [message for message in result.all_messages() if isinstance(message, ModelRequest)]
        return result.output, model_request_list

    with nh.run(StubExecutor()), pytest.raises(ToolBoundaryError, match="tool crashed"):
        anyio.run(run_agent)
    assert tool_call_count == 1


_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_provider_backed_tool_return_preserves_mixed_list_order_via_content_items() -> None:
    """Pin provider-backed ordering for mixed text/multimodal tool returns (spec §8.3).

    Provider-backed Pydantic AI models (OpenAI / Anthropic at the time of
    writing) iterate ``ToolReturnPart.content_items(mode='str')`` directly
    when building the next request. That iteration preserves the original
    list order (pydantic_ai/messages.py, ToolReturnPart.content_items). The
    Nighthawk short-circuit in ``resolve_tool_return_segments`` covers the
    MCP path; this regression test covers the provider-backed path that goes
    through Pydantic AI's ``ToolReturnPart`` directly.
    """
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")
    normalized = _normalize_tool_success(["caption", image, "tail"])

    tool_return_part = ToolReturnPart(tool_name="sample_tool", content=normalized["payload"])
    items = tool_return_part.content_items(mode="str")

    assert items == ["caption", image, "tail"]


def test_tool_success_preserves_multimodal_items_in_list_subclass() -> None:
    class MediaList(list[object]):
        pass

    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")

    normalized = _normalize_tool_success(MediaList(["caption", image, "tail"]))

    assert normalized["payload"] == ["caption", image, "tail"]
    tool_return_part = ToolReturnPart(tool_name="sample_tool", content=normalized["payload"])
    assert tool_return_part.content_items(mode="str") == ["caption", image, "tail"]
