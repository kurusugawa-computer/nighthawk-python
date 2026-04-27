from __future__ import annotations

import json
from collections import namedtuple
from collections.abc import Generator
from pathlib import Path
from typing import Annotated, Any, cast, get_args, get_origin

import anyio
import pytest
import tiktoken
from mcp.types import AudioContent, ImageContent, TextContent
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    ModelRequest,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
    VideoUrl,
    is_multi_modal_content,
    tool_return_ta,
)
from pydantic_ai.messages import (
    TextContent as PydanticTextContent,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends import mcp_boundary as mcp_boundary_module
from nighthawk.backends.base import _collect_request_prompt_part_list
from nighthawk.backends.mcp_boundary import call_tool_for_claude_code_sdk, call_tool_for_low_level_mcp_server
from nighthawk.backends.mcp_server import mcp_server_if_needed
from nighthawk.backends.text_projection import project_request_prompt_part_list_to_text
from nighthawk.backends.tool_bridge import build_tool_name_to_handler
from nighthawk.errors import NighthawkError
from nighthawk.runtime.step_context import StepContext, ToolResultRenderingPolicy
from nighthawk.tools.contracts import (
    ToolBoundaryError,
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
    render_tool_handler_result_preview_text,
)
from nighthawk.tools.execution import ToolResultWrapperToolset
from nighthawk.tools.registry import _reset_all_tools_for_tests, get_visible_tools
from tests.execution.stub_executor import StubExecutor

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    _reset_all_tools_for_tests()


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_tool_boundary",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )


@pytest.fixture
def tool_span_exporter() -> Generator[tuple[InMemorySpanExporter, TracerProvider], None, None]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    yield span_exporter, tracer_provider


def _get_finished_tool_spans(tool_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in tool_span_exporter.get_finished_spans() if (span_data.attributes or {}).get("gen_ai.tool.name") is not None]


def _new_run_context() -> RunContext[StepContext]:
    return RunContext(
        deps=_new_step_context(),
        model=TestModel(),
        usage=RunUsage(),
    )


def _call_wrapped_tool(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
) -> object:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> object:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with set_current_run_context(run_context):
            return await wrapped_toolset.call_tool(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def _call_wrapped_tool_outcome(
    *,
    name: str,
    function: Any,
    tool_args: dict[str, object] | None = None,
) -> ToolOutcome:
    run_context = _new_run_context()
    toolset = FunctionToolset([function])
    wrapped_toolset = ToolResultWrapperToolset(toolset)

    async def run() -> ToolOutcome:
        tool_name_to_tool = await wrapped_toolset.get_tools(run_context)
        tool = tool_name_to_tool[name]
        with set_current_run_context(run_context):
            return await wrapped_toolset.call_tool_outcome(name, tool_args or {}, run_context, tool)

    return anyio.run(run)


def _test_tool_handler_result(
    *,
    payload: object | None,
    error: ToolError | None = None,
) -> ToolHandlerResult:
    tool_outcome: ToolOutcome = {
        "payload": payload,
        "error": error,
    }
    return {
        "kind": "tool_result",
        "tool_outcome": tool_outcome,
    }


def _render_preview_text_for_test(tool_handler_result: ToolHandlerResult) -> str:
    return render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=1_200,
        encoding=tiktoken.get_encoding("o200k_base"),
        style="default",
    )


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


def test_wrapper_call_tool_outcome_converts_tool_exception_to_tool_error() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    tool_outcome = _call_wrapped_tool_outcome(name="test_raises", function=test_raises)
    assert tool_outcome["payload"] is None
    assert tool_outcome["error"] is not None
    assert tool_outcome["error"]["kind"] == "internal"
    assert tool_outcome["error"]["message"] == "boom"


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


def test_wrapper_call_tool_returns_error_envelope_for_tool_exception() -> None:
    def test_raises() -> object:
        raise RuntimeError("boom")

    result = _call_wrapped_tool(name="test_raises", function=test_raises)
    assert result == {
        "value": None,
        "error": {
            "kind": "internal",
            "message": "boom",
            "guidance": "The tool execution raised an unexpected error. Retry or report this error.",
        },
    }


def test_wrapper_call_tool_outcome_converts_oversight_rejection_to_tool_error() -> None:
    tool_call_count = 0

    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        _ = run_context
        tool_call_count += 1
        return 1

    def reject_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.Reject:
        assert tool_call.tool_name == "test_denied_tool"
        return nh.oversight.Reject("human rejected tool")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=reject_tool_call)):
        tool_outcome = _call_wrapped_tool_outcome(name="test_denied_tool", function=test_denied_tool)
    assert tool_outcome["payload"] is None
    assert tool_outcome["error"] is not None
    assert tool_outcome["error"]["kind"] == "oversight"
    assert tool_outcome["error"]["message"] == "human rejected tool"
    assert tool_call_count == 0


def test_wrapper_call_tool_returns_error_envelope_for_oversight_rejection() -> None:
    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return 1

    def reject_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.Reject:
        assert tool_call.tool_name == "test_denied_tool"
        return nh.oversight.Reject("human rejected tool")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=reject_tool_call)):
        result = _call_wrapped_tool(name="test_denied_tool", function=test_denied_tool)

    assert result == {
        "value": None,
        "error": {
            "kind": "oversight",
            "message": "human rejected tool",
            "guidance": "The host rejected this tool call. Choose a different approach or continue without this tool.",
        },
    }


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


def test_collect_request_prompt_part_list_accepts_multimodal_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], BinaryContent)


def test_collect_request_prompt_part_list_accepts_image_url_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([ImageUrl(url="https://example.com/cat.png")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], ImageUrl)


def test_collect_request_prompt_part_list_accepts_audio_url_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert len(request_prompt_part_list) == 1
    assert isinstance(request_prompt_part_list[0], tuple)
    assert isinstance(request_prompt_part_list[0][0], AudioUrl)


def test_collect_request_prompt_part_list_accepts_cache_point_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart(["before", CachePoint(), "after"])])

    request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label="custom-backend")
    assert request_prompt_part_list == [("before", CachePoint(), "after")]


def test_collect_request_prompt_part_list_rejects_uploaded_file_user_prompt_for_custom_backends() -> None:
    model_request = ModelRequest(parts=[UserPromptPart([UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png")])])

    with pytest.raises(UserError, match="UploadedFile user prompt content"):
        _collect_request_prompt_part_list(model_request, backend_label="custom-backend")


def test_project_request_prompt_part_list_to_text_ignores_cache_point() -> None:
    projected_request = project_request_prompt_part_list_to_text([("before", CachePoint(), "after")])

    assert projected_request.prompt_text == "beforeafter"


def test_project_request_prompt_part_list_to_text_projects_tool_return_files_to_local_paths() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="test_image_return",
        content=["caption", BinaryContent(data=b"png", media_type="image/png", identifier="img123")],
    )

    projected_request = project_request_prompt_part_list_to_text([("hello",), tool_return_part])

    try:
        assert "hello" in projected_request.prompt_text
        assert "Tool return from test_image_return:" in projected_request.prompt_text
        assert "caption" in projected_request.prompt_text
        assert "img123" in projected_request.prompt_text
        assert "Local file path:" in projected_request.prompt_text
        assert projected_request.temporary_directory is not None
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_user_prompt_images_to_local_paths() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")]
    )

    try:
        assert "prefix \n<image>\nLocal file path:" in projected_request.prompt_text
        assert "Media type: image/png\n suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is not None
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("media_type", "expected_extension"),
    [
        ("image/png", ".png"),
        ("audio/mpeg", ".mp3"),
        ("application/pdf", ".pdf"),
    ],
)
def test_project_request_prompt_part_list_to_text_uses_deterministic_extensions(media_type: str, expected_extension: str) -> None:
    projected_request = project_request_prompt_part_list_to_text([(BinaryContent(data=b"payload", media_type=media_type, identifier="item"),)])
    try:
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        assert local_file_path_line.endswith(expected_extension)
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_uses_provided_staging_root_directory(tmp_path: Path) -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")],
        staging_root_directory=tmp_path,
    )

    try:
        assert projected_request.temporary_directory is not None
        temporary_directory_path = Path(projected_request.temporary_directory.name).resolve()
        assert temporary_directory_path.parent == tmp_path.resolve()
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
        assert local_file_path.parent == temporary_directory_path
        assert local_file_path.exists()
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_cleanup_removes_staging_directory(tmp_path: Path) -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), " suffix")],
        staging_root_directory=tmp_path,
    )

    assert projected_request.temporary_directory is not None
    temporary_directory_path = Path(projected_request.temporary_directory.name)
    assert temporary_directory_path.exists()

    projected_request.cleanup()

    assert not temporary_directory_path.exists()


def test_project_request_prompt_part_list_to_text_cleans_up_staging_directory_on_projection_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write_bytes = Path.write_bytes
    staged_path_list: list[Path] = []

    def fail_on_second_binary(path: Path, data: bytes) -> int:
        if path.name == "second.png":
            raise OSError("disk full")
        staged_path_list.append(path)
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_on_second_binary)

    with pytest.raises(OSError, match="disk full"):
        project_request_prompt_part_list_to_text(
            [
                (
                    BinaryContent(data=b"first", media_type="image/png", identifier="first"),
                    BinaryContent(data=b"second", media_type="image/png", identifier="second"),
                )
            ],
            staging_root_directory=tmp_path,
        )

    assert staged_path_list
    temporary_directory_path = staged_path_list[0].parent
    assert not temporary_directory_path.exists()


def test_project_request_prompt_part_list_to_text_separates_adjacent_multimodal_items() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [
            (
                BinaryContent(data=b"first", media_type="image/png", identifier="img123"),
                BinaryContent(data=b"second", media_type="image/png", identifier="img456"),
            )
        ]
    )

    try:
        assert "Media type: image/png\n\n<image>\nLocal file path:" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_preserves_tool_return_item_order() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="mixed_tool_return",
        content=["before", BinaryContent(data=b"png", media_type="image/png", identifier="img123"), "after"],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert "Tool return from mixed_tool_return:\nbefore\n<image>\nLocal file path: " in projected_request.prompt_text
        assert "Media type: image/png" in projected_request.prompt_text
        assert "Media type: image/png\nafter" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_preserves_non_multimodal_tool_result_structure() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="structured_tool_return",
        content=["before", {"x": 1}, "after"],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert projected_request.prompt_text == 'Tool return from structured_tool_return:\n["before",{"x":1},"after"]'
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_replaces_uploaded_file_with_fallback_text() -> None:
    tool_return_part = ToolReturnPart(
        tool_name="uploaded_file_tool_return",
        content=[
            "before text",
            UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png", identifier="upload1"),
            "after text",
        ],
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert (
            "Tool return from uploaded_file_tool_return:\nbefore text\n<image>\nUploadedFile: provider=openai, file_id=file-123 (not resolvable by this backend)"
            in projected_request.prompt_text
        )
        assert "Media type: image/png\nafter text" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("payload", "expected_prompt_text"),
    [
        (None, "Tool return from empty_tool:"),
        ("", "Tool return from empty_tool:"),
        ([], "Tool return from empty_tool:"),
        (0, "Tool return from empty_tool:\n0"),
        (False, "Tool return from empty_tool:\nfalse"),
    ],
)
def test_project_request_prompt_part_list_to_text_handles_empty_success_consistently(
    payload: object,
    expected_prompt_text: str,
) -> None:
    tool_return_part = ToolReturnPart(
        tool_name="empty_tool",
        content=payload,
    )

    projected_request = project_request_prompt_part_list_to_text([tool_return_part])

    try:
        assert projected_request.prompt_text == expected_prompt_text
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_sanitizes_binary_content_file_name() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="../escape"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        temporary_directory_path = Path(projected_request.temporary_directory.name).resolve()
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: ")).resolve()
        assert local_file_path.parent == temporary_directory_path
        assert local_file_path.name == "escape.png"
        assert local_file_path.exists()
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_strips_control_characters_from_file_stem() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="hello\x00world\x1f"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert "helloworld" in local_file_path.stem
        assert "\x00" not in local_file_path.name
        assert "\x1f" not in local_file_path.name
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_truncates_long_file_stem() -> None:
    long_identifier = "a" * 300
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier=long_identifier), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert len(local_file_path.stem) <= 200
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_falls_back_for_all_control_character_identifier() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", BinaryContent(data=b"png", media_type="image/png", identifier="\x00\x01"), " suffix")]
    )

    try:
        assert projected_request.temporary_directory is not None
        local_file_path_line = next(line for line in projected_request.prompt_text.splitlines() if line.startswith("Local file path: "))
        local_file_path = Path(local_file_path_line.removeprefix("Local file path: "))
        assert local_file_path.stem == "file"
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_avoids_binary_content_file_name_collisions() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [
            (
                BinaryContent(data=b"first", media_type="image/png", identifier="same"),
                BinaryContent(data=b"second", media_type="image/png", identifier="same"),
            )
        ]
    )

    try:
        local_file_path_list = [
            Path(line.removeprefix("Local file path: ")).resolve()
            for line in projected_request.prompt_text.splitlines()
            if line.startswith("Local file path: ")
        ]
        assert len(local_file_path_list) == 2
        assert local_file_path_list[0] != local_file_path_list[1]
        assert local_file_path_list[0].name == "same.png"
        assert local_file_path_list[1].name == "same_2.png"
        assert local_file_path_list[0].read_bytes() == b"first"
        assert local_file_path_list[1].read_bytes() == b"second"
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_image_url_to_text() -> None:
    projected_request = project_request_prompt_part_list_to_text([("prefix ", ImageUrl(url="https://example.com/cat.png"), " suffix")])

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<image>" in projected_request.prompt_text
        assert "Image URL: https://example.com/cat.png" in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is None
    finally:
        projected_request.cleanup()


def test_project_request_prompt_part_list_to_text_projects_audio_url_to_text() -> None:
    projected_request = project_request_prompt_part_list_to_text(
        [("prefix ", AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg"), " suffix")]
    )

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<file>" in projected_request.prompt_text
        assert "Audio URL: https://example.com/sample.mp3" in projected_request.prompt_text
        assert "Media type: audio/mpeg" in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
        assert projected_request.temporary_directory is None
    finally:
        projected_request.cleanup()


@pytest.mark.parametrize(
    ("url_factory", "expected_label", "url"),
    [
        (AudioUrl, "Audio", "https://example.com/download?id=1"),
        (DocumentUrl, "Document", "https://example.com/download?id=2"),
        (VideoUrl, "Video", "https://example.com/watch?id=3"),
    ],
)
def test_project_request_prompt_part_list_to_text_tolerates_uninferrable_file_url_media_type(
    url_factory: Any,
    expected_label: str,
    url: str,
) -> None:
    projected_request = project_request_prompt_part_list_to_text([("prefix ", url_factory(url=url), " suffix")])

    try:
        assert "prefix " in projected_request.prompt_text
        assert "<file>" in projected_request.prompt_text
        assert f"{expected_label} URL: {url}" in projected_request.prompt_text
        assert "Media type:" not in projected_request.prompt_text
        assert " suffix" in projected_request.prompt_text
    finally:
        projected_request.cleanup()


def test_user_content_union_members_are_covered_by_text_projection_predicate() -> None:
    top_level_member_set = set(get_args(UserContent))
    annotated_member_list = [member for member in top_level_member_set if get_origin(member) is Annotated]

    assert top_level_member_set - set(annotated_member_list) == {
        str,
        PydanticTextContent,
        CachePoint,
    }
    assert len(annotated_member_list) == 1

    multimodal_member_union = get_args(annotated_member_list[0])[0]
    multimodal_member_set = set(get_args(multimodal_member_union))
    assert multimodal_member_set == {
        ImageUrl,
        AudioUrl,
        DocumentUrl,
        VideoUrl,
        BinaryContent,
        UploadedFile,
    }

    for multimodal_member in multimodal_member_set:
        if multimodal_member is BinaryContent:
            content = BinaryContent(data=b"payload", media_type="application/octet-stream")
        elif multimodal_member is UploadedFile:
            content = UploadedFile(file_id="file-123", provider_name="openai")
        else:
            content = multimodal_member(url="https://example.com/media")
        assert is_multi_modal_content(content)


def test_backend_handler_wraps_recoverable_tool_boundary_error() -> None:
    @nh.tool(name="test_boundary_error")
    def test_boundary_error() -> int:  # type: ignore[no-untyped-def]
        raise ToolBoundaryError(
            kind="execution",
            message="tool crashed",
            guidance="Retry with different arguments.",
        )

    step_context = _new_step_context()

    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_boundary_error"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_boundary_error"]

    async def call_handler() -> ToolHandlerResult:
        with set_current_run_context(run_context):
            return await handler({})

    tool_handler_result = anyio.run(call_handler)
    parsed = json.loads(_render_preview_text_for_test(tool_handler_result))
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "execution"
    assert parsed["error"]["message"] == "tool crashed"
    assert parsed["error"]["guidance"] == "Retry with different arguments."
    assert tool_handler_result["kind"] == "tool_result"
    assert tool_handler_result["tool_outcome"]["error"] is not None
    assert tool_handler_result["tool_outcome"]["error"]["kind"] == "execution"


def test_backend_handler_invalid_args_returns_retry_prompt_text() -> None:
    @nh.tool(name="test_arg")
    def test_arg(run_context, *, x: int) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return x

    step_context = _new_step_context()

    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    visible_tools = get_visible_tools()

    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_arg"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_arg"]

    async def call_invalid() -> ToolHandlerResult:
        with set_current_run_context(run_context):
            return await handler({"x": "not an int"})

    tool_handler_result = anyio.run(call_invalid)
    assert tool_handler_result["kind"] == "retry_prompt"
    assert "Fix the errors and try again." in _render_preview_text_for_test(tool_handler_result)


def test_provider_tool_loop_surfaces_tool_failure_as_standard_tool_result() -> None:
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

    output, model_request_list = anyio.run(run_agent)
    assert output == "done"
    assert tool_call_count == 1

    tool_return_part_list = [
        part
        for model_request in model_request_list
        for part in model_request.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "test_failing_tool"
    ]
    assert len(tool_return_part_list) == 1
    assert tool_return_part_list[0].content == {
        "value": None,
        "error": {
            "kind": "execution",
            "message": "tool crashed",
            "guidance": "Retry with different arguments.",
        },
    }


def test_backend_handler_calls_oversight_once_for_accept_decision(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    inspection_count = 0

    @nh.tool(name="test_once_tool")
    def test_once_tool() -> int:  # type: ignore[no-untyped-def]
        return 3

    def accept_tool_call(_tool_call: nh.oversight.ToolCall) -> nh.oversight.Accept:
        nonlocal inspection_count
        inspection_count += 1
        return nh.oversight.Accept()

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
        trace_include_content=True,
        instrumentation_version=1,
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_once_tool"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_once_tool"]

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=accept_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    tool_handler_result = anyio.run(call_handler)
    parsed = json.loads(_render_preview_text_for_test(tool_handler_result))
    assert parsed["value"] == 3
    assert parsed["error"] is None
    assert inspection_count == 1

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "tool_call"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "accept"
    assert oversight_attributes["tool.name"] == "test_once_tool"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.id"] == "test_tool_boundary"

    tool_span_attributes = dict(tool_spans[0].attributes or {})
    trace_payload_candidates: list[dict[str, object]] = []
    for attribute_value in tool_span_attributes.values():
        if not isinstance(attribute_value, str):
            continue
        try:
            parsed_attribute_value = json.loads(attribute_value)
        except Exception:
            continue
        if not isinstance(parsed_attribute_value, dict):
            continue
        if isinstance(parsed_attribute_value, dict) and "has_error" in parsed_attribute_value and "preview_chars" in parsed_attribute_value:
            trace_payload_candidates.append(parsed_attribute_value)

    assert trace_payload_candidates
    assert trace_payload_candidates[0]["has_error"] is False
    assert isinstance(trace_payload_candidates[0]["preview_chars"], int)
    assert trace_payload_candidates[0]["preview_chars"] > 0
    assert "large" not in json.dumps(trace_payload_candidates[0])


def test_backend_handler_raises_nighthawk_error_for_invalid_tool_oversight_decision() -> None:
    @nh.tool(name="test_invalid_tool_decision")
    def test_invalid_tool_decision() -> int:  # type: ignore[no-untyped-def]
        return 5

    def invalid_tool_decision(_tool_call: nh.oversight.ToolCall) -> nh.oversight.ToolCallDecision:
        return cast(Any, "bad")

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_invalid_tool_decision"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_invalid_tool_decision"]

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=invalid_tool_decision)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    with pytest.raises(NighthawkError, match="must return Accept or Reject"):
        anyio.run(call_handler)


def test_backend_handler_preserves_oversight_rejection_and_records_tool_span_event(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    tool_call_count = 0

    @nh.tool(name="test_governed_tool")
    def test_governed_tool() -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        tool_call_count += 1
        return 7

    def reject_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.Reject:
        assert tool_call.argument_name_to_value == {}
        return nh.oversight.Reject("needs oversight")

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
        trace_include_content=True,
        instrumentation_version=1,
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_governed_tool"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_governed_tool"]

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=reject_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    tool_handler_result = anyio.run(call_handler)
    parsed = json.loads(_render_preview_text_for_test(tool_handler_result))
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "oversight"
    assert parsed["error"]["message"] == "needs oversight"
    assert tool_call_count == 0

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "tool_call"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "reject"
    assert oversight_attributes["tool.name"] == "test_governed_tool"
    assert oversight_attributes["nighthawk.oversight.reason"] == "needs oversight"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.id"] == "test_tool_boundary"


def test_mcp_boundary_low_level_mcp_server_returns_text_content_and_propagates_otel_context() -> None:
    parent_otel_context = otel_context.set_value("nighthawk.test", "1", otel_context.get_current())

    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        assert otel_context.get_value("nighthawk.test") == "1"
        return _test_tool_handler_result(payload=2)

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="stub_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=parent_otel_context,
        )

    content = anyio.run(call_boundary)
    assert isinstance(content, list)
    assert len(content) == 1
    item: Any = content[0]
    assert item.type == "text"
    assert item.text == "2"


def test_mcp_boundary_claude_code_returns_text_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=2)

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="stub_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    assert response == {"content": [{"type": "text", "text": "2"}]}


@pytest.mark.parametrize(
    ("payload", "expected_content_length"),
    [
        (None, 0),
        ("", 0),
        ([], 0),
    ],
)
def test_mcp_boundary_low_level_mcp_server_keeps_empty_success_transport_empty(
    payload: object | None,
    expected_content_length: int,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=payload)

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="empty_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert isinstance(content, list)
    assert len(content) == expected_content_length


@pytest.mark.parametrize(
    ("payload",),
    [
        (None,),
        ("",),
        ([],),
    ],
)
def test_mcp_boundary_claude_code_keeps_empty_success_transport_empty(
    payload: object | None,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=payload)

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="empty_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    assert response == {"content": []}


@pytest.mark.parametrize(
    ("payload", "expected_text"),
    [
        (0, "0"),
        (False, "false"),
    ],
)
def test_mcp_boundary_low_level_mcp_server_preserves_non_empty_falsy_success_text(
    payload: object,
    expected_text: str,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=payload)

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="falsy_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert content[0].text == expected_text


@pytest.mark.parametrize(
    ("payload", "expected_text"),
    [
        (0, "0"),
        (False, "false"),
    ],
)
def test_mcp_boundary_claude_code_preserves_non_empty_falsy_success_text(
    payload: object,
    expected_text: str,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=payload)

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="falsy_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    assert response == {"content": [{"type": "text", "text": expected_text}]}


def test_mcp_boundary_low_level_mcp_server_returns_rich_image_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="image_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "See file img1."
    assert isinstance(content[1], TextContent)
    assert content[1].text == "This is file img1:"
    item: Any = content[2]
    assert item.type == "image"
    assert item.mimeType == "image/png"


def test_mcp_boundary_claude_code_returns_rich_image_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
        )

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="image_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert len(content) == 3
    assert content[0] == {"type": "text", "text": "See file img1."}
    assert content[1] == {"type": "text", "text": "This is file img1:"}
    assert content[2]["type"] == "image"
    assert content[2]["mimeType"] == "image/png"


def test_mcp_boundary_low_level_mcp_server_projects_image_url_to_text_content() -> None:
    """FileUrl subclasses project to TextContent on the MCP transport.

    The text shape (``<image>`` placeholder + ``Image URL: ...`` line) mirrors
    the text-projected backend path so the model sees consistent framing across
    transports. Multimodal-capable providers that bypass MCP send the URL
    natively via ``ToolReturnPart.files`` and are not covered here.
    """

    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=ImageUrl(url="https://example.com/cat.png", identifier="img1"),
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="image_url_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "See file img1."
    assert isinstance(content[1], TextContent)
    assert content[1].text == "This is file img1:"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "<image>\nImage URL: https://example.com/cat.png"


def test_mcp_boundary_claude_code_projects_audio_url_to_text_content() -> None:
    """AudioUrl projects to TextContent on claude-code-sdk tool responses."""

    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg", identifier="audio1"),
        )

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="audio_url_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert len(content) == 3
    assert content[0] == {"type": "text", "text": "See file audio1."}
    assert content[1] == {"type": "text", "text": "This is file audio1:"}
    assert content[2] == {
        "type": "text",
        "text": "<file>\nAudio URL: https://example.com/sample.mp3\nMedia type: audio/mpeg",
    }


def test_mcp_boundary_preserves_tool_result_item_order_for_mixed_text_and_images() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=[
                "before",
                BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
                "after",
                BinaryContent(data=b"audio", media_type="audio/mpeg", identifier="audio1"),
            ],
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="mixed_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 4
    assert isinstance(content[0], TextContent)
    assert content[0].text == "before"
    assert isinstance(content[1], ImageContent)
    assert content[1].mimeType == "image/png"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "after"
    assert isinstance(content[3], AudioContent)
    assert content[3].mimeType == "audio/mpeg"


def test_mcp_boundary_claude_code_preserves_tool_result_item_order_for_mixed_text_and_images() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=[
                "before",
                BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
                "after",
                BinaryContent(data=b"audio", media_type="audio/mpeg", identifier="audio1"),
            ],
        )

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="mixed_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert [item["type"] for item in content] == ["text", "image", "text", "audio"]
    assert content[0]["text"] == "before"
    assert content[1]["mimeType"] == "image/png"
    assert content[2]["text"] == "after"
    assert content[3]["mimeType"] == "audio/mpeg"


def test_mcp_boundary_preserves_tool_result_item_order_for_text_content_and_images() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=[
                PydanticTextContent(content="before"),
                BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
                "after",
                BinaryContent(data=b"audio", media_type="audio/mpeg", identifier="audio1"),
            ],
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="mixed_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 4
    assert isinstance(content[0], TextContent)
    assert content[0].text == "before"
    assert isinstance(content[1], ImageContent)
    assert content[1].mimeType == "image/png"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "after"
    assert isinstance(content[3], AudioContent)
    assert content[3].mimeType == "audio/mpeg"


def test_mcp_boundary_claude_code_preserves_tool_result_item_order_for_text_content_and_images() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=[
                PydanticTextContent(content="before"),
                BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
                "after",
                BinaryContent(data=b"audio", media_type="audio/mpeg", identifier="audio1"),
            ],
        )

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="mixed_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert [item["type"] for item in content] == ["text", "image", "text", "audio"]
    assert content[0]["text"] == "before"
    assert content[1]["mimeType"] == "image/png"
    assert content[2]["text"] == "after"
    assert content[3]["mimeType"] == "audio/mpeg"


def test_mcp_boundary_keeps_non_multimodal_structured_list_as_single_text_block() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=["before", {"x": 1}, "after"],
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="structured_list_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert content[0].text == '["before",{"x":1},"after"]'


def test_mcp_boundary_replaces_uploaded_file_with_text_fallback() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=[
                "caption",
                BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img1"),
                UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png", identifier="upload1"),
            ],
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="uploaded_file_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "caption"
    assert isinstance(content[1], ImageContent)
    assert content[1].mimeType == "image/png"
    assert isinstance(content[2], TextContent)
    assert "UploadedFile: provider=openai, file_id=file-123 (not resolvable by MCP transport)" in content[2].text


def test_mcp_boundary_projects_non_image_audio_binary_to_text_content() -> None:
    """Non-image / non-audio ``BinaryContent`` projects to a TextContent fallback.

    The MCP transport used to embed these blobs as an ``EmbeddedResource`` with
    a synthetic ``nighthawk://`` URI, which required a ``cast(Any, ...)`` to
    bypass MCP's ``AnyUrl`` validator and offered no resolvable value to the
    consumer. Non-image / non-audio blobs now project to text, mirroring the
    text-projected backend path. The text body carries the caller-supplied
    identifier and media type verbatim so no URI-safety escaping is required.
    """

    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=BinaryContent(
                data=b"%PDF-1.4",
                media_type="application/pdf",
                identifier="report 2026/Q1",
            ),
        )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="tool/name with space",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    content = anyio.run(call_boundary)
    text_block_list = [item for item in content if isinstance(item, TextContent)]
    assert any(
        block.text
        == "<file>\nBinaryContent: identifier=report 2026/Q1, media_type=application/pdf (embedded by this tool; not resolvable by MCP transport)"
        for block in text_block_list
    )


def test_mcp_boundary_low_level_mcp_server_falls_back_to_preview_when_rich_projection_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=2)

    class RecordingSpan:
        def __init__(self) -> None:
            self.attribute_name_to_value: dict[str, object] = {}

        def is_recording(self) -> bool:
            return True

        def set_attribute(self, name: str, value: object) -> None:
            self.attribute_name_to_value[name] = value

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome) -> list[object]:
        _ = tool_name
        _ = tool_outcome
        raise UserError("projection boom")

    recording_span = RecordingSpan()
    monkeypatch.setattr(mcp_boundary_module, "_tool_outcome_to_mcp_content_list", raise_projection_failure)
    monkeypatch.setattr(mcp_boundary_module, "get_current_span", lambda: recording_span)

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="projection_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    with caplog.at_level("WARNING", logger="nighthawk"):
        content = anyio.run(call_boundary)

    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert content[0].text == '{"value":2,"error":null}'
    assert any(
        "MCP rich projection fallback for tool projection_tool after UserError: projection boom" in record.message for record in caplog.records
    )
    assert recording_span.attribute_name_to_value == {
        "nighthawk.mcp.projection_fallback": True,
        "nighthawk.mcp.projection_fallback.tool_name": "projection_tool",
        "nighthawk.mcp.projection_fallback.exception_type": "UserError",
    }


def test_mcp_boundary_low_level_mcp_server_uses_explicit_rendering_policy_for_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=2)

    captured: dict[str, object] = {}

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome) -> list[object]:
        _ = tool_name
        _ = tool_outcome
        raise UserError("projection boom")

    def fake_render_preview(
        *,
        tool_handler_result: ToolHandlerResult,
        max_tokens: int,
        encoding: tiktoken.Encoding,
        style: str,
    ) -> str:
        captured["kind"] = tool_handler_result["kind"]
        captured["max_tokens"] = max_tokens
        captured["encoding_name"] = encoding.name
        captured["style"] = style
        return "preview from explicit policy"

    monkeypatch.setattr(mcp_boundary_module, "_tool_outcome_to_mcp_content_list", raise_projection_failure)
    monkeypatch.setattr(mcp_boundary_module, "render_tool_handler_result_preview_text", fake_render_preview)

    rendering_policy = ToolResultRenderingPolicy(
        tokenizer_encoding_name="o200k_base",
        tool_result_max_tokens=123,
        json_renderer_style="strict",
    )

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="projection_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
            rendering_policy=rendering_policy,
        )

    content = anyio.run(call_boundary)
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert content[0].text == "preview from explicit policy"
    assert captured == {
        "kind": "tool_result",
        "max_tokens": 123,
        "encoding_name": "o200k_base",
        "style": "strict",
    }


def test_mcp_boundary_claude_code_falls_back_to_preview_when_rich_projection_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=2)

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome) -> list[object]:
        _ = tool_name
        _ = tool_outcome
        raise UserError("projection boom")

    monkeypatch.setattr(mcp_boundary_module, "_tool_outcome_to_mcp_content_list", raise_projection_failure)

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="projection_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    assert response == {"content": [{"type": "text", "text": '{"value":2,"error":null}'}]}


def test_mcp_boundary_claude_code_uses_explicit_rendering_policy_for_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(payload=2)

    captured: dict[str, object] = {}

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome) -> list[object]:
        _ = tool_name
        _ = tool_outcome
        raise UserError("projection boom")

    def fake_render_preview(
        *,
        tool_handler_result: ToolHandlerResult,
        max_tokens: int,
        encoding: tiktoken.Encoding,
        style: str,
    ) -> str:
        captured["kind"] = tool_handler_result["kind"]
        captured["max_tokens"] = max_tokens
        captured["encoding_name"] = encoding.name
        captured["style"] = style
        return "preview from explicit policy"

    monkeypatch.setattr(mcp_boundary_module, "_tool_outcome_to_mcp_content_list", raise_projection_failure)
    monkeypatch.setattr(mcp_boundary_module, "render_tool_handler_result_preview_text", fake_render_preview)

    rendering_policy = ToolResultRenderingPolicy(
        tokenizer_encoding_name="o200k_base",
        tool_result_max_tokens=321,
        json_renderer_style="strict",
    )

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="projection_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
            rendering_policy=rendering_policy,
        )

    response = anyio.run(call_boundary)
    assert response == {"content": [{"type": "text", "text": "preview from explicit policy"}]}
    assert captured == {
        "kind": "tool_result",
        "max_tokens": 321,
        "encoding_name": "o200k_base",
        "style": "strict",
    }


def test_mcp_server_if_needed_rejects_non_step_context_dependencies() -> None:
    async def dummy_tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        _ = arguments
        return _test_tool_handler_result(payload="ok")

    run_context = RunContext(
        deps=object(),
        model=TestModel(),
        usage=RunUsage(),
    )

    async def open_server() -> None:
        with set_current_run_context(run_context):
            async with mcp_server_if_needed(
                tool_name_to_tool_definition={},
                tool_name_to_handler={"dummy_tool": dummy_tool_handler},
            ):
                raise AssertionError("mcp_server_if_needed should fail before yielding")

    with pytest.raises(UnexpectedModelBehavior, match="Codex MCP tool server requires StepContext dependencies"):
        anyio.run(open_server)


def test_mcp_boundary_converts_boundary_exception_to_json_failure_text_without_run_context() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        _ = arguments
        raise RuntimeError("boom")

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
            tool_name="stub_tool",
            arguments={},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    text = content[0]["text"]

    parsed = json.loads(text)
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "internal"
