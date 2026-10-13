from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass

from pydantic_ai.messages import BinaryContent

from nighthawk.runtime.step_context import DEFAULT_TOOL_RESULT_RENDERING_POLICY
from nighthawk.tools._tool_return_content import resolve_tool_return_segments

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_resolve_tool_return_segments_preserves_ordered_mixed_list_verbatim() -> None:
    first_image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="first")
    second_image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="second")
    payload = ["A", first_image, "B", second_image, "C"]

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload, rendering_policy=DEFAULT_TOOL_RESULT_RENDERING_POLICY)

    assert segments.response_text == ""
    assert segments.ordered_user_content == ("A", first_image, "B", second_image, "C")


def test_resolve_tool_return_segments_preserves_ordered_tuple_verbatim() -> None:
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="only")
    payload = ("before", image, "after")

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload, rendering_policy=DEFAULT_TOOL_RESULT_RENDERING_POLICY)

    assert segments.response_text == ""
    assert segments.ordered_user_content == ("before", image, "after")


def test_resolve_tool_return_segments_delegates_dataclass_payload_to_pydantic_ai() -> None:
    @dataclass
    class Report:
        title: str
        score: int

    payload = Report(title="quarterly", score=42)

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload, rendering_policy=DEFAULT_TOOL_RESULT_RENDERING_POLICY)

    # Pydantic AI owns the serialization shape; assert only that the short-circuit was not taken.
    assert segments.response_text != ""
    assert segments.ordered_user_content == ()


def test_resolve_tool_return_segments_delegates_text_only_list_to_pydantic_ai() -> None:
    payload = ["plain", "text", "items"]

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload, rendering_policy=DEFAULT_TOOL_RESULT_RENDERING_POLICY)

    assert segments.ordered_user_content == ()
    assert segments.response_text != ""


def test_resolve_tool_return_segments_delegates_namedtuple_payload_to_pydantic_ai() -> None:
    NamedImageReport = namedtuple("NamedImageReport", ["caption", "image"])
    image = BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png", identifier="img-1")
    payload = NamedImageReport(caption="caption", image=image)

    segments = resolve_tool_return_segments(tool_name="sample_tool", payload=payload, rendering_policy=DEFAULT_TOOL_RESULT_RENDERING_POLICY)

    assert segments.response_text != ""
    assert segments.ordered_user_content == ()
