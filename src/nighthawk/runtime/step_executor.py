from __future__ import annotations

from typing import Any, Protocol, cast, runtime_checkable

from pydantic import TypeAdapter
from pydantic_ai import Agent, StructuredDict
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import StepExecutorConfiguration
from ..errors import ExecutionError
from ..tools.execution import ToolResultWrapperToolset
from ..tools.registry import get_visible_tools
from .async_bridge import run_coroutine_synchronously
from .prompt import build_user_prompt, extract_references_and_program
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_ID,
    get_execution_context,
    get_system_prompt_suffix_fragments,
    scope,
    span,
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
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]: ...


@runtime_checkable
class AsyncStepExecutor(Protocol):
    """Step executor that provides asynchronous execution."""

    async def run_step_async(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]: ...


type StepExecutor = SyncStepExecutor | AsyncStepExecutor


def _new_agent_step_executor(
    configuration: StepExecutorConfiguration,
) -> StepExecutionAgent:
    model_identifier = configuration.model
    provider, provider_model_name = model_identifier.split(":", 1)

    match provider:
        case "claude-code-sdk":
            from ..backends.claude_code_sdk import ClaudeCodeSdkModel

            model: object = ClaudeCodeSdkModel(model_name=(provider_model_name if provider_model_name != "default" else None))
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

    agent = Agent(
        model=model,
        output_type=StepFinalResult,
        deps_type=StepContext,
        system_prompt=configuration.prompts.step_system_prompt_template,
        **constructor_arguments,
    )

    @agent.system_prompt(dynamic=True)
    def _system_prompt_suffixes() -> str | None:  # pyright: ignore[reportUnusedFunction]
        suffix_fragments = (
            *configuration.system_prompt_suffix_fragments,
            *get_system_prompt_suffix_fragments(),
        )
        if not suffix_fragments:
            return None
        return "\n\n".join(suffix_fragments)

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

    async def _run_agent(
        self,
        *,
        user_prompt: str,
        step_context: StepContext,
        toolset: ToolResultWrapperToolset,
        structured_output_type: object,
    ) -> Any:
        if self.agent is None:
            raise ExecutionError("AgentStepExecutor.agent is not initialized")

        if isinstance(self.agent, AsyncExecutionAgent):
            return await self.agent.run(
                user_prompt,
                deps=step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
            )

        if isinstance(self.agent, SyncExecutionAgent):
            return self.agent.run_sync(
                user_prompt,
                deps=step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
            )

        raise ExecutionError("AgentStepExecutor requires an agent with run(...) or run_sync(...)")

    def _build_structured_output_and_prompt_fragment(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        allowed_step_kinds: tuple[str, ...],
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

    def _extract_bindings(
        self,
        *,
        binding_names: list[str],
        step_context: StepContext,
    ) -> dict[str, object]:
        """Extract committed bindings from the step context."""
        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in step_context.assigned_binding_names:
                bindings[name] = step_context.step_locals[name]
        return bindings

    async def run_step_async(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        if step_context.tool_result_rendering_policy is None:
            step_context.tool_result_rendering_policy = self.tool_result_rendering_policy

        user_prompt = build_user_prompt(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
            configuration=self.configuration,
        )

        visible_tool_list = get_visible_tools()
        toolset = ToolResultWrapperToolset(FunctionToolset(visible_tool_list))

        structured_output_type, step_system_prompt_fragment = self._build_structured_output_and_prompt_fragment(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
            allowed_step_kinds=allowed_step_kinds,
        )

        with scope(system_prompt_suffix_fragment=step_system_prompt_fragment):
            execution_context = get_execution_context()
            with (
                span(
                    "nighthawk.step_executor",
                    **{
                        RUN_ID: execution_context.run_id,
                        SCOPE_ID: execution_context.scope_id,
                        STEP_ID: step_context.step_id,
                    },
                ),
                step_context_scope(step_context),
            ):
                result = await self._run_agent(
                    user_prompt=user_prompt,
                    step_context=step_context,
                    toolset=toolset,
                    structured_output_type=structured_output_type,
                )

        step_outcome = self._parse_agent_result(result)
        bindings = self._extract_bindings(binding_names=binding_names, step_context=step_context)
        return step_outcome, bindings

    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        return cast(
            tuple[StepOutcome, dict[str, object]],
            run_coroutine_synchronously(
                lambda: self.run_step_async(
                    processed_natural_program=processed_natural_program,
                    step_context=step_context,
                    binding_names=binding_names,
                    allowed_step_kinds=allowed_step_kinds,
                )
            ),
        )
