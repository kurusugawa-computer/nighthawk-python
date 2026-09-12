from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass

from pydantic_ai.messages import BinaryContent

from nighthawk.tools._tool_return_content import resolve_tool_return_segments

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


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
