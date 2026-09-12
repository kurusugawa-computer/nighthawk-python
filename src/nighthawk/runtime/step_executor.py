from __future__ import annotations

from typing import Any, Protocol, cast, runtime_checkable

from pydantic import TypeAdapter
from pydantic_ai import Agent, RunContext, StructuredDict
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import UserContent
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import ModelSelection, StepExecutorConfiguration
from ..errors import ExecutionError, NighthawkError
from ..tools._admission import ToolAdmissionCapability
from ..tools._ordering import sequential_model_tools
from ..tools.declarations import get_visible_tools
from ..tools.execution import ToolResultWrapperToolset
from .async_bridge import run_coroutine_synchronously
from .context import DefaultContextBuilder, build_context_content, build_context_request
from .model_candidate import resolve_model_candidate
from .prompt import build_system_prompt, extract_references_and_program, resolve_step_system_prompt_template_text
from .scoping import (
    _context_builder_var,
    _current_capabilities,
    _current_system_prompt_suffix_fragments,
    get_usage_meter,
    system_prompt_suffix_fragment_scope,
)
from .step_context import (
    _MISSING,
    StepContext,
    ToolResultRenderingPolicy,
    resolve_name_in_step_context,
    step_context_scope,
)
from .step_contract import (
    STEP_KINDS,
    StepFinalResult,
    StepKind,
    StepOutcome,
    build_step_json_schema,
    build_step_system_prompt_suffix_fragment,
)
from .step_request import StepCandidate, StepRequest


class _OperationFailureGuard(AbstractCapability[StepContext]):
    """Keep failed operations terminal even when model capabilities recover them."""

    # Keep Pydantic AI's keyword-compatible parameter name on this override.
    async def before_model_request(self, ctx: RunContext[StepContext], request_context: ModelRequestContext) -> ModelRequestContext:
        # Pydantic AI can classify a body ValidationError as retryable or let a capability supply a substitute result.
        # Neither clears our operation failure, and neither may cause another model request for this step.
        if ctx.deps.operation_failure is not None:
            raise ctx.deps.operation_failure
        return request_context


@runtime_checkable
class AsyncExecutionAgent(Protocol):
    """Protocol for agents that provide async step execution via ``run``."""

    async def run(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class SyncExecutionAgent(Protocol):
    """Protocol for agents that provide sync step execution via ``run_sync``."""

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


def _new_agent_step_executor(
    configuration: StepExecutorConfiguration,
) -> StepExecutionAgent:
    model_identifier = configuration.model
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

    constructor_arguments: dict[str, Any] = {}
    if configuration.model_settings is not None:
        constructor_arguments["model_settings"] = configuration.model_settings

    system_prompt_text = build_system_prompt(configuration=configuration)

    agent = Agent(
        model=model,
        output_type=StepFinalResult,
        deps_type=StepContext,
        system_prompt=system_prompt_text,
        **constructor_arguments,
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
            resolve_step_system_prompt_template_text(
                template_text=fragment,
                tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
            )
            for fragment in suffix_fragments
        )

    return agent


class AgentStepExecutor:
    """Step executor that delegates Natural block execution to a Pydantic AI agent.

    Attributes:
        configuration: The step executor configuration.
        agent: The underlying agent instance. If not provided, one is created
            from the configuration.
        token_encoding: The tiktoken encoding resolved from the configuration.
        tool_result_rendering_policy: Policy for rendering tool results.
        agent_is_managed: Whether the agent was created internally from
            the configuration (True) or provided externally (False).
    """

    def __init__(
        self,
        configuration: StepExecutorConfiguration | None = None,
        agent: StepExecutionAgent | None = None,
    ) -> None:
        self.configuration = configuration or StepExecutorConfiguration()
        if agent is not None and not isinstance(self.configuration.model, str):
            raise NighthawkError("The external agent owns model selection; configuration.model must be a string")
        self.agent_is_managed = agent is None
        self.agent = agent if agent is not None else _new_agent_step_executor(self.configuration)
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
        configuration: StepExecutorConfiguration | None = None,
    ) -> AgentStepExecutor:
        """Create an executor wrapping an existing agent.

        Args:
            agent: A pre-configured agent to use for step execution.
            configuration: Optional configuration. Defaults to
                StepExecutorConfiguration().
        """
        return cls(configuration=configuration, agent=agent)

    @classmethod
    def from_configuration(
        cls,
        *,
        configuration: StepExecutorConfiguration,
    ) -> AgentStepExecutor:
        """Create an executor from a configuration, building a managed agent internally."""
        return cls(configuration=configuration)

    def resolve_model_selection(self, selection: ModelSelection) -> StepExecutor:
        """Resolve selection; subclasses must explicitly implement preserving resolution."""
        if type(self) is not AgentStepExecutor:
            raise NighthawkError("AgentStepExecutor subclasses must implement a preserving resolve_model_selection")
        configuration = selection.apply(self.configuration)
        if self.agent_is_managed:
            return AgentStepExecutor.from_configuration(configuration=configuration)
        if not isinstance(configuration.model, str) or configuration.model != self.configuration.model:
            raise NighthawkError("The external agent owns model selection; scope cannot switch its model")
        if self.agent is None:
            raise NighthawkError("AgentStepExecutor.agent is not initialized")
        return AgentStepExecutor.from_agent(agent=self.agent, configuration=configuration)

    async def _run_agent(
        self,
        *,
        user_prompt: tuple[UserContent, ...],
        step_context: StepContext,
        toolset: ToolResultWrapperToolset,
        structured_output_type: object,
    ) -> Any:
        if self.agent is None:
            raise ExecutionError("AgentStepExecutor.agent is not initialized")

        normalized_user_prompt: str | tuple[UserContent, ...] = (
            user_prompt[0] if len(user_prompt) == 1 and isinstance(user_prompt[0], str) else user_prompt
        )
        capabilities = [*_current_capabilities(), _OperationFailureGuard(), ToolAdmissionCapability()]

        if isinstance(self.agent, AsyncExecutionAgent):
            return await self.agent.run(
                normalized_user_prompt,
                deps=step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
                capabilities=capabilities,
            )

        if isinstance(self.agent, SyncExecutionAgent):
            return self.agent.run_sync(
                normalized_user_prompt,
                deps=step_context,
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
        allowed_step_kinds: tuple[StepKind, ...],
    ) -> tuple[object, str]:
        """Build the structured output type and system prompt fragment for a step."""
        unknown_kinds = set(allowed_step_kinds).difference(STEP_KINDS)
        if unknown_kinds:
            raise ExecutionError(f"Internal error: allowed_step_kinds contains unknown kinds: {tuple(sorted(unknown_kinds))}")

        allowed_kinds_deduplicated = tuple(dict.fromkeys(allowed_step_kinds))
        allowed_kinds_typed = cast(tuple[StepKind, ...], allowed_kinds_deduplicated)

        referenced_names, _ = extract_references_and_program(processed_natural_program)

        error_type_candidate_names: set[str] = set(referenced_names)
        for name, value in step_context.step_locals.items():
            if isinstance(value, type) and issubclass(value, BaseException) and value.__name__ == name:
                error_type_candidate_names.add(name)

        error_type_binding_name_list: list[str] = []
        for name in sorted(error_type_candidate_names):
            value = resolve_name_in_step_context(step_context, name)
            if value is _MISSING:
                continue
            if not isinstance(value, type) or not issubclass(value, BaseException) or value.__name__ != name:
                continue
            error_type_binding_name_list.append(name)

        raise_error_type_binding_names = tuple(error_type_binding_name_list)

        step_system_prompt_fragment = build_step_system_prompt_suffix_fragment(
            allowed_kinds=allowed_kinds_typed,
            raise_error_type_binding_names=raise_error_type_binding_names,
        )

        outcome_json_schema = build_step_json_schema(
            allowed_kinds=allowed_kinds_typed,
            raise_error_type_binding_names=raise_error_type_binding_names,
        )
        structured_output_type = StructuredDict(outcome_json_schema, name="StepFinalResult")

        return structured_output_type, step_system_prompt_fragment

    def _parse_agent_result(
        self,
        result: Any,
    ) -> StepOutcome:
        """Parse the agent result into a StepOutcome."""
        try:
            raw_output = result.output
            if isinstance(raw_output, StepFinalResult):
                return raw_output.result
            if isinstance(raw_output, dict) and "result" in raw_output:
                return TypeAdapter(StepOutcome).validate_python(raw_output["result"])
            return TypeAdapter(StepOutcome).validate_python(raw_output)
        except Exception as e:
            raise ExecutionError(f"Step produced invalid step outcome: {e}") from e

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        step_context = request.step_context
        processed_natural_program = request.processed_natural_program
        allowed_step_kinds = request.allowed_step_kinds
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
            allowed_step_kinds=allowed_step_kinds,
        )

        with (
            system_prompt_suffix_fragment_scope(step_system_prompt_fragment),
            step_context_scope(step_context),
            sequential_model_tools(),
        ):
            try:
                result = await self._run_agent(
                    user_prompt=user_prompt,
                    step_context=step_context,
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
            raise step_context.operation_failure

        usage_meter = get_usage_meter()
        if usage_meter is not None and hasattr(result, "usage"):
            usage_meter.record(result.usage, kind="step")

        step_outcome = self._parse_agent_result(result)
        return await resolve_model_candidate(request, step_outcome, {})

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))
