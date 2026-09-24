"""``UserContent`` hoisting shared by the user-prompt and tool-return paths."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeGuard, cast

from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    TextContent,
    UploadedFile,
    UserContent,
    VideoUrl,
    is_multi_modal_content,
)


def coalesce_user_content(content_list: Sequence[UserContent]) -> tuple[UserContent, ...]:
    coalesced_content_list: list[UserContent] = []
    pending_text_part_list: list[str] = []

    def flush_pending_text() -> None:
        if pending_text_part_list:
            coalesced_content_list.append("".join(pending_text_part_list))
            pending_text_part_list.clear()

    for content in content_list:
        if isinstance(content, str):
            pending_text_part_list.append(content)
            continue
        if isinstance(content, TextContent):
            pending_text_part_list.append(content.content)
            continue
        flush_pending_text()
        coalesced_content_list.append(content)

    flush_pending_text()
    return tuple(coalesced_content_list)


def is_top_level_sequence_payload(value: object) -> TypeGuard[list[object] | tuple[object, ...]]:
    """Namedtuples are records; hoisting them would discard field names."""
    if isinstance(value, list):
        return True
    return isinstance(value, tuple) and not hasattr(value, "_fields")


def try_project_ordered_user_content_sequence(payload: object) -> tuple[UserContent, ...] | None:
    """Bare multimodal scalars are left to the caller so the tool-return path can delegate them to Pydantic AI."""
    if not is_top_level_sequence_payload(payload):
        return None

    content_part_list: list[UserContent] = []
    contains_multimodal_content = False
    for item in payload:
        if isinstance(item, str):
            content_part_list.append(item)
            continue
        if isinstance(item, TextContent):
            content_part_list.append(item)
            continue
        if is_multi_modal_content(item):
            content_part_list.append(cast(UserContent, item))
            contains_multimodal_content = True
            continue
        return None

    if not contains_multimodal_content:
        return None
    return coalesce_user_content(content_part_list)


def try_project_user_prompt_value(value: object) -> tuple[UserContent, ...] | None:
    """Unlike the tool-return path, which keeps Pydantic AI's ``"See file {id}."`` framing, bare multimodal scalars hoist here."""
    if any(type(value) is candidate for candidate in (BinaryContent, ImageUrl, AudioUrl, DocumentUrl, VideoUrl, UploadedFile)):
        return (cast(UserContent, value),)
    if type(value) is not list and type(value) is not tuple:
        return None
    if any(
        not any(type(item) is candidate for candidate in (str, TextContent, BinaryContent, ImageUrl, AudioUrl, DocumentUrl, VideoUrl, UploadedFile))
        for item in value
    ):
        return None
    return try_project_ordered_user_content_sequence(value)
