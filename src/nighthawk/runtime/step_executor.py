from __future__ import annotations

import json
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import TypeAdapter
from pydantic_ai import Agent, AgentRunResult, RunContext, StructuredDict, models
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import UserContent
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.decision import DecisionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.output import OutputContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_core import to_json

from ..composition import UNSET, UnsetType
from ..configuration import ModelSelection, StepExecutorConfiguration
from ..errors import ExecutionError, NighthawkError
from ..tools._admission import ToolAdmissionCapability
from ..tools._ordering import sequential_model_tools
from ..tools.declarations import get_visible_tools
from ..tools.execution import ToolResultWrapperToolset
from ._user_content import try_project_user_prompt_value
from .async_bridge import run_coroutine_synchronously
from .context import DefaultContextBuilder, build_context_content, build_context_request
from .model_candidate import resolve_model_candidate
from .model_outcome import (
    ModelOutcome,
    ModelStepResponse,
    build_model_response_instructions,
    build_model_response_schema,
)
from .model_response import get_response_rejection, record_response_rejection
from .operation_contract import OperationRejectedError
from .prompt import build_system_prompt, extract_references_and_program, resolve_system_prompt_template_text
from .scoping import (
    _context_builder_var,
    _current_capabilities,
    _current_system_prompt_suffix_fragments,
    get_usage_meter,
    system_prompt_suffix_fragment_scope,
)
from .step_context import (
    StepContext,
    ToolResultRenderingPolicy,
    resolve_name_in_step_context,
    step_context_scope,
)
from .step_outcome import OUTCOME_KINDS, OutcomeKind, Pass
from .step_request import StepCandidate, StepRequest


def _require_decision_model(model: Model) -> None:
    while isinstance(model, WrapperModel):
        model = model.wrapped
    if not isinstance(model, DecisionModel):
        raise NighthawkError("decision_model must be a Pydantic AI DecisionModel")


class _DecisionBoundary(AbstractCapability[StepContext]):
    """Keep decisions limited to typed answers, including under scoped capabilities."""

    async def before_model_request(self, ctx: RunContext[StepContext], request_context: ModelRequestContext) -> ModelRequestContext:
        _require_decision_model(request_context.model)
        parameters = request_context.model_request_parameters
        if parameters.function_tools or parameters.native_tools:
            raise NighthawkError("decide cannot expose action tools")
        return request_context


class _OperationFailureGuard(AbstractCapability[StepContext]):
    """Keep failed operations terminal even when model capabilities recover them."""

    # Keep Pydantic AI's keyword-compatible parameter name on this override.
    async def before_model_request(self, ctx: RunContext[StepContext], request_context: ModelRequestContext) -> ModelRequestContext:
        # Pydantic AI can classify a body ValidationError as retryable or let a capability supply a substitute result.
        # Neither clears our operation failure, and neither may cause another model request for this step.
        if ctx.deps.operation_failure is not None:
            raise ctx.deps.operation_failure from ctx.deps.operation_failure.__cause__
        return request_context


class _StepOutputBoundary(_OperationFailureGuard):
    """Resolve candidate operations while the agent can still receive feedback."""

    def __init__(self, request: StepRequest) -> None:
        super().__init__()
        self.request = request

    async def after_output_process(self, ctx: RunContext[StepContext], *, output_context: OutputContext, output: Any) -> Any:
        if ctx.partial_output:
            return output
        if ctx.deps.operation_failure is not None:
            raise ctx.deps.operation_failure from ctx.deps.operation_failure.__cause__
        rejection = get_response_rejection(ctx.deps)
        if rejection is not None:
            raise ModelRetry(json.dumps(rejection.model_feedback(), ensure_ascii=False))
        try:
            return await resolve_model_candidate(self.request, _parse_model_outcome(output))
        except OperationRejectedError as exception:
            if ctx.deps.operation_failure is not None:
                raise ctx.deps.operation_failure from ctx.deps.operation_failure.__cause__
            record_response_rejection(ctx.deps, exception.rejection)
            raise ModelRetry(json.dumps(exception.rejection.model_feedback(), ensure_ascii=False)) from exception


def _parse_model_outcome(raw_output: Any) -> ModelOutcome:
    try:
        if isinstance(raw_output, ModelStepResponse):
            return raw_output.result
        if isinstance(raw_output, dict) and "result" in raw_output:
            return TypeAdapter(ModelOutcome).validate_python(raw_output["result"])
        return TypeAdapter(ModelOutcome).validate_python(raw_output)
    except Exception as exception:
        raise ExecutionError(f"Step produced invalid step outcome: {exception}") from exception


@runtime_checkable
class AsyncExecutionAgent(Protocol):
    async def run(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class SyncExecutionAgent(Protocol):
    def run_sync(self, *args: Any, **kwargs: Any) -> Any: ...


type StepExecutionAgent = AsyncExecutionAgent | SyncExecutionAgent


@runtime_checkable
class SyncStepExecutor(Protocol):
    """Step executor that provides synchronous execution."""

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate: ...


@runtime_checkable
class AsyncStepExecutor(Protocol):
    """Step executor that provides asynchronous execution."""

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate: ...


type StepExecutor = SyncStepExecutor | AsyncStepExecutor


@runtime_checkable
class ConfigurableStepExecutor(Protocol):
    """Optional resolution owned by the active executor, including its host controls."""

    def resolve_model_selection(self, selection: ModelSelection) -> StepExecutor: ...


def _create_execution_agent(
    configuration: StepExecutorConfiguration,
) -> StepExecutionAgent:
    model_identifier = configuration.execution_model
    if model_identifier is None:
        raise NighthawkError("execute requires execution_model in run or scope")
    model: object = model_identifier
    if isinstance(model_identifier, str):
        provider, provider_model_name = model_identifier.split(":", 1)

        match provider:
            case "claude-code-sdk":
                from ..backends.claude_code_sdk import ClaudeCodeSdkModel

                model = ClaudeCodeSdkModel(model_name=(provider_model_name if provider_model_name != "default" else None))
            case "claude-code-cli":
                from ..backends.claude_code_cli import ClaudeCodeCliModel

                model = ClaudeCodeCliModel(model_name=(provider_model_name if provider_model_name != "default" else None))
            case "codex":
                from ..backends.codex import CodexModel

                model = CodexModel(model_name=(provider_model_name if provider_model_name != "default" else None))
            case _:
                model = model_identifier

    system_prompt_text = build_system_prompt(configuration=configuration)

    agent = Agent(
        model=model,
        output_type=ModelStepResponse,
        deps_type=StepContext,
        system_prompt=system_prompt_text,
        model_settings=cast(ModelSettings, configuration.execution_model_settings),
    )

    @agent.system_prompt(dynamic=True)
    def _system_prompt_suffixes() -> str | None:  # pyright: ignore[reportUnusedFunction]
        suffix_fragments = (
            *configuration.system_prompt_suffix_fragments,
            *_current_system_prompt_suffix_fragments(),
        )
        if not suffix_fragments:
            return None
        return "\n\n".join(
            resolve_system_prompt_template_text(
                template_text=fragment,
                tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
            )
            for fragment in suffix_fragments
        )

    return agent


class AgentStepExecutor:
    """Execute Natural blocks using managed agents or a borrowed execution agent.

    The constructor manages execution model selection through configuration.
    Use ``from_agent`` when an existing agent owns execute model selection.
    In both cases, decide uses the independently configured decision model.

    Attributes:
        configuration: The step executor configuration.
        agent: The execution agent, or None until an execution model is selected.
        token_encoding: The tiktoken encoding resolved from the configuration.
        tool_result_rendering_policy: Policy for rendering tool results.
        agent_is_managed: Whether the agent was created internally from
            the configuration (True) or provided externally (False).
    """

    def __init__(
        self,
        configuration: StepExecutorConfiguration | UnsetType = UNSET,
    ) -> None:
        self._initialize(configuration=configuration, agent=None)

    def _initialize(
        self,
        *,
        configuration: StepExecutorConfiguration | UnsetType,
        agent: StepExecutionAgent | None,
    ) -> None:
        if not isinstance(configuration, (StepExecutorConfiguration, UnsetType)):
            raise TypeError("configuration must be StepExecutorConfiguration or UNSET")
        self.configuration = StepExecutorConfiguration() if isinstance(configuration, UnsetType) else configuration
        if agent is not None and self.configuration.execution_model is not None and not isinstance(self.configuration.execution_model, str):
            raise NighthawkError("The external agent owns model selection; configuration.execution_model must be a string")
        self.agent_is_managed = agent is None
        self.agent = (
            agent if agent is not None else (_create_execution_agent(self.configuration) if self.configuration.execution_model is not None else None)
        )
        self.token_encoding = self.configuration.resolve_token_encoding()
        self.tool_result_rendering_policy = ToolResultRenderingPolicy(
            tokenizer_encoding_name=self.token_encoding.name,
            tool_result_max_tokens=(self.configuration.context_limits.tool_result_max_tokens),
            json_renderer_style=self.configuration.json_renderer_style,
        )

    @classmethod
    def from_agent(
        cls,
        *,
        agent: StepExecutionAgent,
        configuration: StepExecutorConfiguration | UnsetType = UNSET,
    ) -> AgentStepExecutor:
        """Borrow an existing agent for execute blocks without constructing another.

        The supplied agent owns execute model selection and its resource lifetime.
        Decide blocks use configuration.decision_model independently.

        Args:
            agent: A pre-configured agent to use for step execution.
            configuration: Omission or UNSET creates StepExecutorConfiguration().
                None and raw dictionaries are invalid; parse configuration data explicitly.
        """
        if not isinstance(agent, (AsyncExecutionAgent, SyncExecutionAgent)):
            raise TypeError("agent must implement the synchronous or asynchronous agent protocol")
        executor = cls.__new__(cls)
        executor._initialize(configuration=configuration, agent=agent)
        return executor

    def resolve_model_selection(self, selection: ModelSelection) -> StepExecutor:
        """Resolve selection; subclasses must explicitly implement preserving resolution."""
        if type(self) is not AgentStepExecutor:
            raise NighthawkError("AgentStepExecutor subclasses must implement a preserving resolve_model_selection")
        configuration = selection.apply(self.configuration)
        if self.agent_is_managed:
            return AgentStepExecutor(configuration=configuration)
        if configuration.execution_model != self.configuration.execution_model:
            raise NighthawkError("The external agent owns model selection; scope cannot switch its model")
        if self.agent is None:
            raise NighthawkError("AgentStepExecutor.agent is not initialized")
        return AgentStepExecutor.from_agent(agent=self.agent, configuration=configuration)

    async def _run_decision(self, request: StepRequest) -> StepCandidate:
        selected_model = self.configuration.decision_model
        if selected_model is None:
            raise NighthawkError("decide requires decision_model in run or scope")
        model = models.infer_model(selected_model)
        _require_decision_model(model)
        output_name = request.output_binding_names[0]
        output_type = request.binding_name_to_type[output_name]
        # Full, explicitly referenced evidence only. Execution previews are lossy and require tool rereads.
        content: list[UserContent] = []
        for name, value in request.input_binding_name_to_value.items():
            projected = try_project_user_prompt_value(value)
            content.append(f"Binding <{name}>:\n")
            if projected is not None:
                content.extend(projected)
            else:
                content.append(to_json(value).decode())
            content.append("\n")
        if not content:
            content.append("No input bindings.")
        agent = Agent(
            model=model,
            output_type=cast(Any, output_type),
            deps_type=StepContext,
            instructions=extract_references_and_program(request.processed_natural_program)[1],
            model_settings=cast(ModelSettings, self.configuration.decision_model_settings),
        )
        with step_context_scope(request.step_context):
            result = await agent.run(content, deps=request.step_context, capabilities=[*_current_capabilities(), _DecisionBoundary()])
        # Accessing response requires a real response from this invocation, even if a capability supplies output.
        _ = result.response
        request.step_context.model_result = result
        get_usage_meter().record(result.usage)
        return StepCandidate(outcome=Pass(), binding_name_to_value={output_name: result.output})

    async def _run_agent(
        self,
        *,
        user_prompt: tuple[UserContent, ...],
        request: StepRequest,
        toolset: ToolResultWrapperToolset,
        structured_output_type: object,
    ) -> Any:
        if self.agent is None:
            raise ExecutionError("AgentStepExecutor.agent is not initialized")

        normalized_user_prompt: str | tuple[UserContent, ...] = (
            user_prompt[0] if len(user_prompt) == 1 and isinstance(user_prompt[0], str) else user_prompt
        )
        # First means last for after_output_process: resolve the output after application hooks.
        capabilities = [_StepOutputBoundary(request), *_current_capabilities(), ToolAdmissionCapability()]

        if isinstance(self.agent, AsyncExecutionAgent):
            return await self.agent.run(
                normalized_user_prompt,
                deps=request.step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
                capabilities=capabilities,
            )

        if isinstance(self.agent, SyncExecutionAgent):
            return self.agent.run_sync(
                normalized_user_prompt,
                deps=request.step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
                capabilities=capabilities,
            )

        raise ExecutionError("AgentStepExecutor requires an agent with run(...) or run_sync(...)")

    def _build_structured_output_and_prompt_fragment(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        allowed_outcome_kinds: tuple[OutcomeKind, ...],
    ) -> tuple[object, str]:
        unknown_kinds = set(allowed_outcome_kinds).difference(OUTCOME_KINDS)
        if unknown_kinds:
            raise ExecutionError(f"Internal error: allowed_outcome_kinds contains unknown kinds: {tuple(sorted(unknown_kinds))}")

        allowed_kinds_deduplicated = tuple(dict.fromkeys(allowed_outcome_kinds))
        allowed_kinds_typed = cast(tuple[OutcomeKind, ...], allowed_kinds_deduplicated)

        referenced_names, _ = extract_references_and_program(processed_natural_program)

        error_type_binding_name_list: list[str] = []
        for name in sorted(set(referenced_names) | step_context.step_locals.keys()):
            value = resolve_name_in_step_context(step_context, name)
            if not isinstance(value, type) or not issubclass(value, BaseException):
                continue
            error_type_binding_name_list.append(name)

        raise_error_type_binding_names = tuple(error_type_binding_name_list)

        step_system_prompt_fragment = build_model_response_instructions(
            allowed_kinds=allowed_kinds_typed,
            raise_error_type_binding_names=raise_error_type_binding_names,
        )

        outcome_json_schema = build_model_response_schema(
            allowed_kinds=allowed_kinds_typed,
            raise_error_type_binding_names=raise_error_type_binding_names,
        )
        structured_output_type = StructuredDict(outcome_json_schema, name="ModelStepResponse")

        return structured_output_type, step_system_prompt_fragment

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        if request.block_kind == "decide":
            return await self._run_decision(request)
        if self.agent is None:
            raise NighthawkError("execute requires execution_model in run or scope")
        step_context = request.step_context
        processed_natural_program = request.processed_natural_program
        allowed_outcome_kinds = request.allowed_outcome_kinds
        if step_context.tool_result_rendering_policy is None:
            step_context.tool_result_rendering_policy = self.tool_result_rendering_policy

        context_request = build_context_request(request, self.configuration)
        context_builder = _context_builder_var.get()
        if context_builder is None:
            context_builder = DefaultContextBuilder()
        user_prompt = build_context_content(context_request, context_builder)

        visible_tool_list = get_visible_tools()
        toolset = ToolResultWrapperToolset(FunctionToolset(visible_tool_list))

        structured_output_type, step_system_prompt_fragment = self._build_structured_output_and_prompt_fragment(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
            allowed_outcome_kinds=allowed_outcome_kinds,
        )

        with (
            system_prompt_suffix_fragment_scope(step_system_prompt_fragment),
            step_context_scope(step_context),
            sequential_model_tools(),
        ):
            try:
                result = await self._run_agent(
                    user_prompt=user_prompt,
                    request=request,
                    toolset=toolset,
                    structured_output_type=structured_output_type,
                )
            except Exception:
                failure = step_context.operation_failure
                if failure is None:
                    raise
                # Preserve the operation exception and its original cause instead of a secondary retry-limit or capability exception.
                raise failure from failure.__cause__

        if step_context.operation_failure is not None:
            raise step_context.operation_failure from step_context.operation_failure.__cause__

        if isinstance(result, AgentRunResult):
            step_context.model_result = result
        if hasattr(result, "usage"):
            get_usage_meter().record(result.usage)

        if isinstance(result.output, StepCandidate):
            return result.output
        # Protocol agents that do not execute Pydantic AI capabilities own their model loop.
        return await resolve_model_candidate(request, _parse_model_outcome(result.output))

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))
