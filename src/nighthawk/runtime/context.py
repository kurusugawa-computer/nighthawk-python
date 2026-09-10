"""Replaceable presentation of one live Natural environment."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, InstanceOf, field_validator
from pydantic_ai.messages import CachePoint, UserContent, is_multi_modal_content

from ..configuration import StepContextLimits, StepExecutorConfiguration
from .environment import StepEnvironment
from .execution_reference import ExecutionReference
from .step_context import StepContext
from .step_request import StepRequest


class ContextRequest(BaseModel):
    """Presentation inputs and contracts without copying the environment.

    ``environment`` is the same live environment used by runtime operations.
    A builder may choose what to present but must preserve its name lookup and
    application objects. ``configuration`` supplies prompt templates, rendering
    policy, and the budgets exposed by ``context_limits``.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    execution_reference: ExecutionReference
    processed_natural_program: str
    step_context: InstanceOf[StepContext]
    explicit_reference_names: tuple[str, ...]
    explicit_reference_paths: tuple[str, ...]
    output_binding_names: tuple[str, ...]
    binding_name_to_type: Mapping[str, object]
    return_annotation: object
    configuration: StepExecutorConfiguration

    @field_validator("binding_name_to_type", mode="after")
    @classmethod
    def freeze_contracts(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))

    @property
    def environment(self) -> StepEnvironment:
        return self.step_context.environment

    @property
    def context_limits(self) -> StepContextLimits:
        return self.configuration.context_limits


@runtime_checkable
class ContextBuilder(Protocol):
    """Build synchronous prompt content; asynchronous retrieval belongs in tools."""

    def build(self, request: ContextRequest) -> Sequence[UserContent]: ...


class DefaultContextBuilder:
    """Present the environment using configured templates and context budgets."""

    def build(self, request: ContextRequest) -> Sequence[UserContent]:
        from .prompt import _build_user_prompt_content

        return _build_user_prompt_content(
            processed_natural_program=request.processed_natural_program,
            step_context=request.step_context,
            configuration=request.configuration,
        )


def build_context_request(request: StepRequest, configuration: StepExecutorConfiguration) -> ContextRequest:
    from .prompt import extract_reference_data_and_program

    references, paths, _ = extract_reference_data_and_program(request.processed_natural_program)
    return ContextRequest(
        execution_reference=request.execution_reference,
        processed_natural_program=request.processed_natural_program,
        step_context=request.step_context,
        explicit_reference_names=tuple(sorted(references)),
        explicit_reference_paths=tuple(sorted(paths)),
        output_binding_names=request.output_binding_names,
        binding_name_to_type=request.binding_name_to_type,
        return_annotation=request.return_annotation,
        configuration=configuration,
    )


def build_context_content(request: ContextRequest, builder: ContextBuilder) -> tuple[UserContent, ...]:
    import inspect

    from .prompt import append_user_prompt_suffixes

    content = builder.build(request)
    if inspect.iscoroutine(content):
        content.close()
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes, bytearray)):
        raise TypeError("ContextBuilder.build must return a sequence of Pydantic AI UserContent")
    if any(not isinstance(item, (str, CachePoint)) and not is_multi_modal_content(item) for item in content):
        raise TypeError("ContextBuilder.build returned an unsupported UserContent item")
    return append_user_prompt_suffixes(tuple(content), configuration=request.configuration)
