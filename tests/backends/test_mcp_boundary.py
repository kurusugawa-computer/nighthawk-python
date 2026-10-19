from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
import tiktoken
from mcp.types import AudioContent, ContentBlock, ImageContent, TextContent
from opentelemetry import context as otel_context
from pydantic_ai import RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    ImageUrl,
    UploadedFile,
)
from pydantic_ai.messages import (
    TextContent as PydanticTextContent,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from nighthawk.backends import mcp_boundary as mcp_boundary_module
from nighthawk.backends.mcp_boundary import call_tool_for_claude_code_sdk, call_tool_for_low_level_mcp_server
from nighthawk.backends.mcp_server import mcp_server_if_needed
from nighthawk.runtime import StepEnvironment
from nighthawk.runtime.step_context import ToolResultRenderingPolicy, step_context_scope
from nighthawk.tools.contracts import (
    ToolHandlerResult,
    ToolOutcome,
)
from tests.support.contexts import new_context
from tests.support.tools import _test_tool_handler_result

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("budget", [64, 128, 512])
@pytest.mark.parametrize("explicit", [False, True])
def test_discovery_content_uses_selected_rendering_policy(budget: int, explicit: bool) -> None:
    context = new_context()
    context.tool_result_rendering_policy = ToolResultRenderingPolicy("o200k_base", 2_000 if explicit else budget, "default")
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})
    with step_context_scope(context):
        content = mcp_boundary_module.tool_handler_result_to_low_level_mcp_content(
            tool_name="discovery",
            tool_handler_result=_test_tool_handler_result(payload=environment.discover(source="task")),
            rendering_policy=ToolResultRenderingPolicy("o200k_base", budget, "default") if explicit else None,
        )
    assert len(content) == 1 and isinstance(content[0], TextContent)
    observed = json.loads(content[0].text)
    assert len(tiktoken.get_encoding("o200k_base").encode(json.dumps(observed, separators=(",", ":")))) <= budget
    if budget == 64:
        assert "recovery" in observed
    else:
        namespace = environment.expression_namespace()
        next_page = eval(observed["continuation"], namespace, namespace)
        assert next_page["names"]


def test_mcp_boundary_low_level_mcp_server_returns_text_content_and_propagates_otel_context() -> None:
    parent_otel_context = otel_context.set_value("nighthawk.test", "1", otel_context.get_current())

    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        assert otel_context.get_value("nighthawk.test") == "1"
        return _test_tool_handler_result(payload=2)

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="stub_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=parent_otel_context,
            )
        ).content

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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="empty_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="falsy_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="image_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "See file img1."
    assert isinstance(content[1], TextContent)
    assert content[1].text == "This is file img1:"
    item: Any = content[2]
    assert item.type == "image"
    assert item.mime_type == "image/png"


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
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=ImageUrl(url="https://example.com/cat.png", identifier="img1"),
        )

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="image_url_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "See file img1."
    assert isinstance(content[1], TextContent)
    assert content[1].text == "This is file img1:"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "<image>\nImage URL: https://example.com/cat.png"


def test_mcp_boundary_claude_code_projects_audio_url_to_text_content() -> None:
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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="mixed_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

    content = anyio.run(call_boundary)
    assert len(content) == 4
    assert isinstance(content[0], TextContent)
    assert content[0].text == "before"
    assert isinstance(content[1], ImageContent)
    assert content[1].mime_type == "image/png"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "after"
    assert isinstance(content[3], AudioContent)
    assert content[3].mime_type == "audio/mpeg"


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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="mixed_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

    content = anyio.run(call_boundary)
    assert len(content) == 4
    assert isinstance(content[0], TextContent)
    assert content[0].text == "before"
    assert isinstance(content[1], ImageContent)
    assert content[1].mime_type == "image/png"
    assert isinstance(content[2], TextContent)
    assert content[2].text == "after"
    assert isinstance(content[3], AudioContent)
    assert content[3].mime_type == "audio/mpeg"


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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="structured_list_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="uploaded_file_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

    content = anyio.run(call_boundary)
    assert len(content) == 3
    assert isinstance(content[0], TextContent)
    assert content[0].text == "caption"
    assert isinstance(content[1], ImageContent)
    assert content[1].mime_type == "image/png"
    assert isinstance(content[2], TextContent)
    assert "UploadedFile: provider=openai, file_id=file-123 (not resolvable by MCP transport)" in content[2].text


def test_mcp_boundary_projects_non_image_audio_binary_to_text_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        assert arguments == {"x": 1}
        return _test_tool_handler_result(
            payload=BinaryContent(
                data=b"%PDF-1.4",
                media_type="application/pdf",
                identifier="report 2026/Q1",
            ),
        )

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="tool/name with space",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

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

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome, rendering_policy: ToolResultRenderingPolicy) -> list[ContentBlock]:
        _ = tool_name
        _ = tool_outcome
        raise UserError("projection boom")

    recording_span = RecordingSpan()
    monkeypatch.setattr(mcp_boundary_module, "_tool_outcome_to_mcp_content_list", raise_projection_failure)
    monkeypatch.setattr(mcp_boundary_module, "get_current_span", lambda: recording_span)

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="projection_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
            )
        ).content

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

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome, rendering_policy: ToolResultRenderingPolicy) -> list[ContentBlock]:
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

    async def call_boundary() -> list[ContentBlock]:
        return (
            await call_tool_for_low_level_mcp_server(
                tool_name="projection_tool",
                arguments={"x": 1},
                tool_handler=tool_handler,
                parent_otel_context=otel_context.get_current(),
                rendering_policy=rendering_policy,
            )
        ).content

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

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome, rendering_policy: ToolResultRenderingPolicy) -> list[ContentBlock]:
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

    def raise_projection_failure(*, tool_name: str, tool_outcome: ToolOutcome, rendering_policy: ToolResultRenderingPolicy) -> list[ContentBlock]:
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


@pytest.mark.parametrize("boundary", [call_tool_for_claude_code_sdk, call_tool_for_low_level_mcp_server])
@pytest.mark.parametrize("failure", [RuntimeError("boom"), ModelRetry("retry"), ApprovalRequired(), CallDeferred()])
def test_mcp_boundary_preserves_application_exceptions_and_explicit_control_signals(boundary: Any, failure: Exception) -> None:
    async def tool_handler(arguments: dict[str, object]) -> ToolHandlerResult:
        _ = arguments
        raise failure

    async def call_boundary() -> dict[str, object]:
        return await boundary(
            tool_name="stub_tool",
            arguments={},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    original_context = otel_context.get_current()
    with pytest.raises(type(failure)) as caught:
        anyio.run(call_boundary)
    assert caught.value is failure
    assert otel_context.get_current() is original_context
