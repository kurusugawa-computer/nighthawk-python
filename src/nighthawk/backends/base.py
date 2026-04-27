from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
    is_multi_modal_content,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from ..configuration import TEXT_PROJECTED_TOOL_RESULT_PREVIEW_SYSTEM_PROMPT_FRAGMENT
from ..runtime.prompt import resolve_step_system_prompt_template_text
from .tool_bridge import ToolHandler, prepare_allowed_tools, resolve_current_tool_result_rendering_policy

if TYPE_CHECKING:
    from .text_projection import TextProjectedRequest

type RequestPromptPart = tuple[UserContent, ...] | ToolReturnPart
type RequestPromptPartList = list[RequestPromptPart]


def _validate_coding_agent_user_prompt_content_item(*, item: UserContent, backend_label: str) -> None:
    """Enforce the coding-agent-backend user-prompt admission policy.

    This layers two checks:

    1. Structural check (policy-free): reject anything outside the Pydantic AI
       ``UserContent`` union (``str | TextContent | CachePoint | <multimodal>``).
    2. Admission policy (coding-agent-specific): reject ``UploadedFile`` even
       though it is a legal ``UserContent`` member, because coding-agent
       backends cannot resolve provider-owned file references.

    Only backends subclassing :class:`BackendModelBase` (the coding-agent
    backends) route through this helper; provider-backed executors send
    ``UserContent`` natively and bypass this path.
    """
    if isinstance(item, UploadedFile):
        raise UserError(
            f"{backend_label} does not support UploadedFile user prompt content; provider-owned uploaded files cannot be resolved by this backend"
        )
    if isinstance(item, str | TextContent | CachePoint) or is_multi_modal_content(item):
        return
    raise UserError(f"{backend_label} does not support user prompt content type {type(item).__name__}")


def _find_most_recent_model_request(messages: list[ModelMessage]) -> ModelRequest:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return message
    raise UnexpectedModelBehavior("No ModelRequest found in message history")


def _collect_system_prompt_text(model_request: ModelRequest) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, SystemPromptPart) and part.content:
            parts.append(part.content)
    return "\n\n".join(parts)


def _resolve_current_tool_result_max_tokens() -> int:
    return resolve_current_tool_result_rendering_policy().tool_result_max_tokens


def append_text_projected_tool_result_preview_prompt(*, system_prompt_text: str) -> str:
    """Append the text-projected tool-result preview warning to a system prompt.

    Backends should call this only after confirming that at least one Nighthawk
    tool will actually be exposed to the model. If no tool is exposed, the
    preview-loss caveat is irrelevant and adds prompt noise.
    """
    fragment = resolve_step_system_prompt_template_text(
        template_text=TEXT_PROJECTED_TOOL_RESULT_PREVIEW_SYSTEM_PROMPT_FRAGMENT,
        tool_result_max_tokens=_resolve_current_tool_result_max_tokens(),
    )
    if not system_prompt_text:
        return fragment
    return "\n".join([system_prompt_text, fragment])


@dataclass(frozen=True)
class PreparedRequestParts:
    system_prompt_text: str
    request_prompt_part_list: RequestPromptPartList


@dataclass(frozen=True)
class PreparedTextProjectedRequest:
    system_prompt_text: str
    user_prompt_text: str
    projected_request: TextProjectedRequest


def _normalize_user_prompt_content(content: str | Sequence[UserContent], *, backend_label: str) -> tuple[UserContent, ...]:
    if isinstance(content, str):
        return (content,)

    normalized_content_list: list[UserContent] = []
    for item in content:
        _validate_coding_agent_user_prompt_content_item(item=item, backend_label=backend_label)
        normalized_content_list.append(item)

    return tuple(normalized_content_list)


def _collect_request_prompt_part_list(model_request: ModelRequest, *, backend_label: str) -> RequestPromptPartList:
    request_prompt_part_list: RequestPromptPartList = []
    for part in model_request.parts:
        if isinstance(part, UserPromptPart):
            request_prompt_part_list.append(_normalize_user_prompt_content(part.content, backend_label=backend_label))
        elif isinstance(part, RetryPromptPart):
            request_prompt_part_list.append((part.model_response(),))
        elif isinstance(part, ToolReturnPart):
            request_prompt_part_list.append(part)

    return request_prompt_part_list


class BackendModelBase(Model):
    """Shared request prelude for backends that expose Nighthawk tools via Pydantic AI FunctionToolset.

    Provider-specific backends should:
    - call `prepare_request(...)` and then `_prepare_common_request_parts(...)`
    - call `_prepare_allowed_tools(...)` to get filtered tool definitions/handlers
    - handle provider-specific transport/execution and convert to `ModelResponse`
    """

    backend_label: str

    def __init__(self, *, backend_label: str, profile: Any) -> None:
        super().__init__(profile=profile)
        self.backend_label = backend_label

    def _prepare_common_request_parts(
        self,
        *,
        messages: list[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> PreparedRequestParts:
        if model_request_parameters.builtin_tools:
            raise UserError(f"{self.backend_label} does not support builtin tools")

        if model_request_parameters.allow_image_output:
            raise UserError(f"{self.backend_label} does not support image output")

        model_request = _find_most_recent_model_request(messages)

        system_prompt_text = _collect_system_prompt_text(model_request)

        instructions = self._get_instructions(messages, model_request_parameters)
        if instructions:
            system_prompt_text = "\n\n".join([system_prompt_text, instructions]) if system_prompt_text else instructions

        request_prompt_part_list = _collect_request_prompt_part_list(model_request, backend_label=self.backend_label)
        return PreparedRequestParts(
            system_prompt_text=system_prompt_text,
            request_prompt_part_list=request_prompt_part_list,
        )

    def _prepare_text_projected_request(
        self,
        *,
        messages: list[ModelMessage],
        model_request_parameters: ModelRequestParameters,
        staging_root_directory: Path,
        empty_prompt_exception_factory: Callable[[str], Exception],
    ) -> PreparedTextProjectedRequest:
        from .text_projection import project_request_prompt_part_list_to_text

        prepared_request_parts = self._prepare_common_request_parts(
            messages=messages,
            model_request_parameters=model_request_parameters,
        )
        system_prompt_text = prepared_request_parts.system_prompt_text

        projected_request = project_request_prompt_part_list_to_text(
            prepared_request_parts.request_prompt_part_list,
            staging_root_directory=staging_root_directory,
        )
        user_prompt_text = projected_request.prompt_text
        if user_prompt_text.strip() == "":
            projected_request.cleanup()
            raise empty_prompt_exception_factory(f"{self.backend_label} requires a non-empty user prompt")

        return PreparedTextProjectedRequest(
            system_prompt_text=system_prompt_text,
            user_prompt_text=user_prompt_text,
            projected_request=projected_request,
        )

    async def _prepare_allowed_tools(
        self,
        *,
        model_request_parameters: ModelRequestParameters,
        configured_allowed_tool_names: tuple[str, ...] | None,
        visible_tools: list[Any],
    ) -> tuple[dict[str, Any], dict[str, ToolHandler], tuple[str, ...]]:
        return await prepare_allowed_tools(
            model_request_parameters=model_request_parameters,
            configured_allowed_tool_names=configured_allowed_tool_names,
            visible_tools=visible_tools,
        )


class BackendModelSettings(BaseModel):
    """Base settings shared by all Nighthawk backends.

    Attributes:
        allowed_tool_names: Nighthawk tool names exposed to the model.
        working_directory: Absolute path to the working directory.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_tool_names: tuple[str, ...] | None = None
    working_directory: str = ""

    @field_validator("working_directory")
    @classmethod
    def _validate_working_directory(cls, value: str) -> str:
        if value and not Path(value).is_absolute():
            raise ValueError("working_directory must be an absolute path")
        return value

    @classmethod
    def from_model_settings(cls, model_settings: ModelSettings | None) -> Self:
        """Parse a pydantic_ai ModelSettings dict into a typed settings instance."""
        if model_settings is None:
            return cls()
        try:
            return cls.model_validate(model_settings)
        except Exception as exception:
            raise UserError(str(exception)) from exception
