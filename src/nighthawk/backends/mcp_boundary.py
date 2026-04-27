from __future__ import annotations

import logging
from typing import cast

import tiktoken
from opentelemetry import context as otel_context
from opentelemetry.context import Context as OtelContext
from opentelemetry.trace import get_current_span
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import BinaryContent, FileUrl, TextContent, UploadedFile

from ..runtime.step_context import (
    DEFAULT_TOOL_RESULT_RENDERING_POLICY,
    ToolResultRenderingPolicy,
    get_current_step_context,
    resolve_tool_result_rendering_policy,
)
from ..tools._tool_return_content import resolve_tool_return_segments
from ..tools.contracts import (
    BoundaryFailureObservation,
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
    ToolResultObservation,
    build_tool_result_observation,
    render_tool_handler_result_preview_text,
)
from ._text_fallback import (
    format_file_url_with_placeholder,
    format_unresolvable_binary_content_with_placeholder,
    format_unresolvable_content_text,
)
from .tool_bridge import ToolHandler

_logger = logging.getLogger("nighthawk")


def _resolve_tool_result_rendering_policy() -> ToolResultRenderingPolicy:
    try:
        step_context = get_current_step_context()
    except Exception:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return resolve_tool_result_rendering_policy(step_context.tool_result_rendering_policy)


def _resolve_effective_tool_result_rendering_policy(
    *,
    rendering_policy: ToolResultRenderingPolicy | None,
) -> ToolResultRenderingPolicy:
    if rendering_policy is not None:
        return rendering_policy
    return _resolve_tool_result_rendering_policy()


def _tool_boundary_failure_result(*, message: str, guidance: str) -> ToolResultObservation:
    tool_outcome: ToolOutcome = {
        "payload": None,
        "error": {"kind": "internal", "message": message, "guidance": guidance},
    }
    return build_tool_result_observation(tool_outcome=tool_outcome)


def _tool_outcome_to_mcp_content_list(
    *,
    tool_name: str,
    tool_outcome: ToolOutcome,
) -> list[object]:
    from mcp import types as mcp_types

    content_block_list: list[object] = []

    segments = resolve_tool_return_segments(
        tool_name=tool_name,
        payload=tool_outcome["payload"],
    )
    if segments.is_empty_success():
        return []

    if segments.response_text:
        content_block_list.append(mcp_types.TextContent(type="text", text=segments.response_text))

    for content_item in segments.ordered_user_content:
        if isinstance(content_item, str):
            if content_item:
                content_block_list.append(mcp_types.TextContent(type="text", text=content_item))
            continue
        if isinstance(content_item, TextContent):
            if content_item.content:
                content_block_list.append(mcp_types.TextContent(type="text", text=content_item.content))
            continue

        if isinstance(content_item, BinaryContent):
            if content_item.is_image:
                content_block_list.append(mcp_types.ImageContent(type="image", data=content_item.base64, mimeType=content_item.media_type))
                continue
            if content_item.is_audio:
                content_block_list.append(mcp_types.AudioContent(type="audio", data=content_item.base64, mimeType=content_item.media_type))
                continue
            # Non-image / non-audio blobs project to text per spec §8.3:
            # MCP's rich transport only has first-class slots for image and
            # audio media, and Nighthawk intentionally does NOT wrap other
            # blobs in an ``EmbeddedResource`` with a synthetic URI scheme.
            content_block_list.append(
                mcp_types.TextContent(
                    type="text",
                    text="\n".join(
                        format_unresolvable_binary_content_with_placeholder(
                            content=content_item,
                            transport_label="MCP transport",
                        )
                    ),
                )
            )
            continue

        if isinstance(content_item, FileUrl):
            # ImageUrl / AudioUrl / DocumentUrl / VideoUrl all project to text so
            # the MCP transport stays symmetric with text-projected backends.
            # Multimodal-capable providers that bypass MCP send these natively
            # via ``ToolReturnPart.files``.
            content_block_list.append(
                mcp_types.TextContent(
                    type="text",
                    text="\n".join(format_file_url_with_placeholder(content=content_item)),
                )
            )
            continue

        if isinstance(content_item, UploadedFile):
            fallback_text = format_unresolvable_content_text(
                content=content_item,
                transport_label="MCP transport",
            )
            content_block_list.append(mcp_types.TextContent(type="text", text=fallback_text))
            continue

        raise UserError(f"MCP tool transport does not support tool return content type {type(content_item).__name__}")

    return content_block_list


def _record_mcp_projection_fallback(*, tool_name: str, exception: Exception) -> None:
    _logger.warning(
        "MCP rich projection fallback for tool %s after %s: %s",
        tool_name,
        type(exception).__name__,
        exception,
    )

    current_span = get_current_span()
    if not current_span.is_recording():
        return

    current_span.set_attribute("nighthawk.mcp.projection_fallback", True)
    current_span.set_attribute("nighthawk.mcp.projection_fallback.tool_name", tool_name)
    current_span.set_attribute(
        "nighthawk.mcp.projection_fallback.exception_type",
        type(exception).__name__,
    )


def tool_handler_result_to_low_level_mcp_content(
    *,
    tool_name: str,
    tool_handler_result: ToolHandlerResult,
    rendering_policy: ToolResultRenderingPolicy | None = None,
) -> list[object]:
    from mcp import types as mcp_types

    effective_rendering_policy = _resolve_effective_tool_result_rendering_policy(
        rendering_policy=rendering_policy,
    )
    encoding = tiktoken.get_encoding(effective_rendering_policy.tokenizer_encoding_name)

    def render_preview() -> str:
        return render_tool_handler_result_preview_text(
            tool_handler_result=tool_handler_result,
            max_tokens=effective_rendering_policy.tool_result_max_tokens,
            encoding=encoding,
            style=effective_rendering_policy.json_renderer_style,
        )

    if tool_handler_result["kind"] != "tool_result":
        return [mcp_types.TextContent(type="text", text=render_preview())]

    tool_outcome = tool_handler_result["tool_outcome"]
    if tool_outcome["error"] is not None:
        return [mcp_types.TextContent(type="text", text=render_preview())]

    try:
        return _tool_outcome_to_mcp_content_list(
            tool_name=tool_name,
            tool_outcome=tool_outcome,
        )
    except Exception as exception:
        _record_mcp_projection_fallback(tool_name=tool_name, exception=exception)
        return [mcp_types.TextContent(type="text", text=render_preview())]


def _tool_handler_result_to_claude_code_sdk_content(
    *,
    tool_name: str,
    tool_handler_result: ToolHandlerResult,
    rendering_policy: ToolResultRenderingPolicy | None = None,
) -> dict[str, object]:
    content_block_list = tool_handler_result_to_low_level_mcp_content(
        tool_name=tool_name,
        tool_handler_result=tool_handler_result,
        rendering_policy=rendering_policy,
    )

    dumped_content_block_list: list[dict[str, object]] = []
    for content_block in content_block_list:
        model_dump = getattr(content_block, "model_dump", None)
        if callable(model_dump):
            dumped_content_block_list.append(cast(dict[str, object], model_dump(mode="json", by_alias=True, exclude_none=True)))
            continue
        raise TypeError(f"Unsupported MCP content block: {type(content_block).__name__}")

    return {"content": dumped_content_block_list}


async def _get_safe_tool_handler_result(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> ToolHandlerResult:
    try:
        context_token = otel_context.attach(parent_otel_context)
        try:
            try:
                return await tool_handler(arguments)
            except Exception as exception:
                error_message = str(exception) or "Tool boundary wrapper failed"
                return _tool_boundary_failure_result(
                    message=error_message,
                    guidance="The tool boundary wrapper failed. Retry or report this error.",
                )
        finally:
            otel_context.detach(context_token)
    except Exception as exception:
        tool_error: ToolError = {
            "kind": "internal",
            "message": str(exception) or "Tool boundary wrapper failed",
            "guidance": "The tool boundary wrapper failed. Retry or report this error.",
        }
        return BoundaryFailureObservation(
            kind="boundary_failure",
            tool_error=tool_error,
        )


async def call_tool_for_claude_code_sdk(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
    rendering_policy: ToolResultRenderingPolicy | None = None,
) -> dict[str, object]:
    tool_handler_result = await _get_safe_tool_handler_result(
        tool_name=tool_name,
        arguments=arguments,
        tool_handler=tool_handler,
        parent_otel_context=parent_otel_context,
    )
    return _tool_handler_result_to_claude_code_sdk_content(
        tool_name=tool_name,
        tool_handler_result=tool_handler_result,
        rendering_policy=rendering_policy,
    )


async def call_tool_for_low_level_mcp_server(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
    rendering_policy: ToolResultRenderingPolicy | None = None,
) -> list[object]:
    tool_handler_result = await _get_safe_tool_handler_result(
        tool_name=tool_name,
        arguments=arguments,
        tool_handler=tool_handler,
        parent_otel_context=parent_otel_context,
    )
    return tool_handler_result_to_low_level_mcp_content(
        tool_name=tool_name,
        tool_handler_result=tool_handler_result,
        rendering_policy=rendering_policy,
    )
