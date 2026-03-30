from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, SystemPromptPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from .tool_bridge import ToolHandler, prepare_allowed_tools


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


def _collect_user_prompt_text(model_request: ModelRequest, *, backend_label: str) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, UserPromptPart):
            if isinstance(part.content, str):
                parts.append(part.content)
            else:
                raise UserError(f"{backend_label} does not support non-text user prompts")
        elif isinstance(part, RetryPromptPart):
            parts.append(part.model_response())
        elif isinstance(part, ToolReturnPart):
            raise UserError(f"{backend_label} does not support tool-return parts")

    return "\n\n".join(p for p in parts if p)


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
    ) -> tuple[ModelRequest, str, str]:
        if model_request_parameters.builtin_tools:
            raise UserError(f"{self.backend_label} does not support builtin tools")

        if model_request_parameters.allow_image_output:
            raise UserError(f"{self.backend_label} does not support image output")

        model_request = _find_most_recent_model_request(messages)

        system_prompt_text = _collect_system_prompt_text(model_request)

        instructions = self._get_instructions(messages, model_request_parameters)
        if instructions:
            system_prompt_text = "\n\n".join([system_prompt_text, instructions]) if system_prompt_text else instructions

        user_prompt_text = _collect_user_prompt_text(model_request, backend_label=self.backend_label)
        if user_prompt_text.strip() == "":
            raise UserError(f"{self.backend_label} requires a non-empty user prompt")

        return model_request, system_prompt_text, user_prompt_text

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
