"""Keep mixed text and media ``list``/``tuple`` tool returns in their original order.

Pydantic AI's ``ToolReturnPart.model_response_str_and_user_content`` moves multimodal items to a trailing block.
This module short-circuits those payloads and delegates every other shape to Pydantic AI.
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic_ai.messages import ToolReturnPart, UserContent

from ..json_renderer import project_discovery_value
from ..runtime._user_content import try_project_ordered_user_content_sequence
from ..runtime.discovery import contains_discovery_result


class ToolReturnSegments(NamedTuple):
    response_text: str
    ordered_user_content: tuple[UserContent, ...]

    def is_empty_success(self) -> bool:
        """Transports keep an empty success empty instead of falling back to the projected preview."""
        return self.response_text == "" and not self.ordered_user_content


def resolve_tool_return_segments(
    *,
    tool_name: str,
    payload: object,
) -> ToolReturnSegments:
    if contains_discovery_result(payload):
        import tiktoken

        from ..backends.tool_bridge import resolve_current_tool_result_rendering_policy

        policy = resolve_current_tool_result_rendering_policy()
        projected = project_discovery_value(
            payload, max_tokens=policy.tool_result_max_tokens, encoding=tiktoken.get_encoding(policy.tokenizer_encoding_name)
        )
        assert projected is not None
        payload = projected.value
    # Revisit if a future Pydantic AI release preserves adjacency natively.
    ordered_user_content_tuple = try_project_ordered_user_content_sequence(payload)
    if ordered_user_content_tuple is not None:
        return ToolReturnSegments(response_text="", ordered_user_content=ordered_user_content_tuple)
    tool_return_part = ToolReturnPart(tool_name=tool_name, content=payload)
    response_text, user_content_list = tool_return_part.model_response_str_and_user_content()
    return ToolReturnSegments(response_text=response_text, ordered_user_content=tuple(user_content_list))
