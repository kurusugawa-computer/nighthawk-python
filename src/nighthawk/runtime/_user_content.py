"""Shared ``UserContent`` projection helpers used across the prompt and tool-return paths.

These helpers operate purely on the Pydantic AI ``UserContent`` union and the
hoisting rules from spec §8.2.2 / §8.3. Backend-specific text rendering and
tool-return segmentation live in their respective modules and import from
here as needed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeGuard, cast

from pydantic_ai.messages import TextContent, UserContent, is_multi_modal_content


def coalesce_user_content(content_list: Sequence[UserContent]) -> tuple[UserContent, ...]:
    """Collapse adjacent ``str``/``TextContent`` items into single strings.

    ``TextContent.content`` values are merged into the surrounding string run;
    non-text content is preserved as-is and terminates the current run.
    """
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
    """Return True for top-level list/tuple payloads that should preserve order.

    ``namedtuple`` instances are records, not anonymous content sequences. Do
    not hoist them as ordered user content because doing so would discard field
    names such as ``image=...`` / ``caption=...``.
    """
    if isinstance(value, list):
        return True
    return isinstance(value, tuple) and not hasattr(value, "_fields")


def try_project_ordered_user_content_sequence(payload: object) -> tuple[UserContent, ...] | None:
    """List/tuple-only projector: return coalesced user-content when *payload* hoists.

    Used by both the user-prompt path and the tool-return path. Bare scalar
    multimodal values are intentionally not handled here so the tool-return
    path can delegate them to Pydantic AI (pinned by
    ``tests/tools/test_tool_boundary.py``).
    """
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
    """User-prompt-path projector: return a user-content tuple when *value* hoists inline.

    Short-circuits a bare multimodal scalar to ``(value,)``. The list/tuple
    path delegates to :func:`try_project_ordered_user_content_sequence` and
    returns ``None`` for any non-hoistable shape.

    Difference from the tool-return path: bare scalars hoist here, but on the
    tool-return path they are delegated to Pydantic AI so its
    ``"See file {id}."`` framing is preserved.
    """
    if is_multi_modal_content(value):
        return (cast(UserContent, value),)
    return try_project_ordered_user_content_sequence(value)
