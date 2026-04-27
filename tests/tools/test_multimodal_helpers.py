"""Regression tests for tool-return segmentation and text-fallback helpers.

These pin the contract between Nighthawk's tool-return segmentation and
Pydantic AI's ``ToolReturnPart.model_response_str_and_user_content()``:

- Mixed ``list[UserContent]`` payloads MUST preserve the original top-level
  ordering. Nighthawk short-circuits Pydantic AI's extraction to guarantee
  this, because that API does not preserve text/media adjacency.
- All other payload shapes (dataclasses, plain objects, ...) MUST be
  delegated to Pydantic AI's canonical extraction.
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass

import pytest
from pydantic_ai.messages import AudioUrl, BinaryContent, DocumentUrl, FileUrl, ImageUrl, ToolReturnPart, VideoUrl

from nighthawk.backends._text_fallback import format_file_url_as_text_lines
from nighthawk.tools._tool_return_content import resolve_tool_return_segments
from nighthawk.tools.execution import _normalize_tool_success

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


class GenericFileUrl(FileUrl):
    def _infer_media_type(self) -> str:
        return "application/octet-stream"

    @property
    def format(self) -> str:
        return "bin"


def test_resolve_tool_return_segments_preserves_ordered_mixed_list_verbatim() -> None:
    """Mixed text + media lists keep their top-level order (short-circuit path)."""
    first_image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="first")
    second_image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="second")
    payload = ["A", first_image, "B", second_image, "C"]

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload)

    assert segments.response_text == ""
    assert segments.ordered_user_content == ("A", first_image, "B", second_image, "C")


def test_resolve_tool_return_segments_preserves_ordered_tuple_verbatim() -> None:
    """Tuple payloads follow the same short-circuit rule as list payloads."""
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="only")
    payload = ("before", image, "after")

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload)

    assert segments.response_text == ""
    assert segments.ordered_user_content == ("before", image, "after")


def test_resolve_tool_return_segments_delegates_dataclass_payload_to_pydantic_ai() -> None:
    """Non-sequence payloads flow through ToolReturnPart extraction instead of short-circuit."""

    @dataclass
    class Report:
        title: str
        score: int

    payload = Report(title="quarterly", score=42)

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload)

    # Pydantic AI renders dataclasses into a textual response; the exact
    # serialization shape is owned by pydantic-ai, so we only assert that the
    # short-circuit was NOT taken (ordered_user_content empty, response_text set).
    assert segments.response_text != ""
    assert segments.ordered_user_content == ()


def test_resolve_tool_return_segments_delegates_text_only_list_to_pydantic_ai() -> None:
    """Pure text-only sequences are not treated as ordered multimodal content."""
    payload = ["plain", "text", "items"]

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload)

    # Short-circuit requires at least one multimodal item; a text-only list is
    # delegated to pydantic-ai and does NOT preserve item-by-item ordering in
    # ordered_user_content.
    assert segments.ordered_user_content == ()
    assert segments.response_text != ""


def test_resolve_tool_return_segments_delegates_namedtuple_payload_to_pydantic_ai() -> None:
    """Namedtuple payloads are records, not anonymous ordered UserContent sequences."""
    NamedImageReport = namedtuple("NamedImageReport", ["caption", "image"])
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")
    payload = NamedImageReport(caption="caption", image=image)

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload)

    assert segments.response_text != ""
    assert segments.ordered_user_content == ()


@pytest.mark.parametrize(
    ("content", "expected_lines"),
    [
        (
            ImageUrl(url="https://example.com/cat.png"),
            ["Image URL: https://example.com/cat.png"],
        ),
        (
            AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg"),
            ["Audio URL: https://example.com/sample.mp3", "Media type: audio/mpeg"],
        ),
        (
            DocumentUrl(url="https://example.com/report.pdf"),
            ["Document URL: https://example.com/report.pdf", "Media type: application/pdf"],
        ),
        (
            VideoUrl(url="https://example.com/movie.mp4"),
            ["Video URL: https://example.com/movie.mp4", "Media type: video/mp4"],
        ),
        (
            GenericFileUrl(url="https://example.com/archive.bin", media_type="application/octet-stream"),
            ["File URL: https://example.com/archive.bin", "Media type: application/octet-stream"],
        ),
        (
            VideoUrl(url="https://example.com/watch?id=3"),
            ["Video URL: https://example.com/watch?id=3"],
        ),
    ],
)
def test_format_file_url_as_text_lines(content: FileUrl, expected_lines: list[str]) -> None:
    assert format_file_url_as_text_lines(content=content) == expected_lines


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
