"""Tool-return segmentation: split a successful payload into text + ordered user content.

This is the Nighthawk-side correction layer over Pydantic AI's
``ToolReturnPart.model_response_str_and_user_content``. The Pydantic AI helper
groups multimodal items into a trailing block and does not preserve the
original top-level adjacency that spec §8.3 requires for mixed text/media
``list`` / ``tuple`` payloads. This module short-circuits those payloads so
ordering survives, and falls back to Pydantic AI for everything else.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic_ai.messages import ToolReturnPart, UserContent

from ..runtime._user_content import try_project_ordered_user_content_sequence


class ToolReturnSegments(NamedTuple):
    """Backend-neutral split of a successful tool-return payload.

    Semantically corresponds to the ``(response_text, user_content)`` split
    returned by Pydantic AI's
    :meth:`pydantic_ai.messages.ToolReturnPart.model_response_str_and_user_content`,
    with the additional Nighthawk guarantee that the top-level ordering of a
    mixed text/multimodal ``list`` / ``tuple`` payload is preserved (see
    spec §8.3). The field name ``ordered_user_content`` reflects that
    ordering guarantee.

    Use :meth:`is_empty_success` at backend transports to detect Pydantic AI's
    empty-success case (spec §8.3: keep the transport empty rather than
    falling back to the projected preview).
    """

    response_text: str
    ordered_user_content: tuple[UserContent, ...]

    def is_empty_success(self) -> bool:
        """Return True when the successful split carries no text and no user content."""
        return self.response_text == "" and not self.ordered_user_content


def resolve_tool_return_segments(
    *,
    tool_name: str,
    payload: object,
) -> ToolReturnSegments:
    """Return the backend-neutral segmentation for a successful tool-return payload.

    Ordered multimodal ``list``/``tuple`` payloads are returned verbatim with an
    empty textual prefix. All other shapes are delegated to Pydantic AI's
    ``ToolReturnPart.model_response_str_and_user_content``.
    """
    # Spec §8.3 requires top-level text/media adjacency to survive on mixed
    # ``list[UserContent]`` payloads. Pydantic AI's
    # ``model_response_str_and_user_content()`` reorders multimodal items to a
    # trailing block, so we short-circuit here. Revisit if a future Pydantic AI
    # release preserves adjacency natively. Both halves of this contract are
    # pinned by ``tests/tools/test_tool_boundary.py``.
    ordered_user_content_tuple = try_project_ordered_user_content_sequence(payload)
    if ordered_user_content_tuple is not None:
        return ToolReturnSegments(response_text="", ordered_user_content=ordered_user_content_tuple)
    tool_return_part = ToolReturnPart(tool_name=tool_name, content=payload)
    response_text, user_content_list = tool_return_part.model_response_str_and_user_content()
    return ToolReturnSegments(response_text=response_text, ordered_user_content=tuple(user_content_list))
