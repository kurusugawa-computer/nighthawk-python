"""Convenient model selection over the explicit Core execution boundary."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

from nighthawk.core import scoping
from nighthawk.core.configuration import UNSET, Extend, Merge, UnsetType
from nighthawk.core.model_selection import ModelSelection, model_selection
from nighthawk.core.scoping import Scope
from nighthawk.core.step_executor import StepExecutor
from nighthawk.core.usage import UsageMeter

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model

    from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
    from nighthawk.adapters.pydantic_ai.context import ContextBuilder
    from nighthawk.adapters.pydantic_ai.tools.declarations import ToolEntry
    from nighthawk.core.lifecycle import StepLifecycle
    from nighthawk.core.oversight import Oversight
    from nighthawk.core.step_context import StepContext


def _model_selection(execution_model: object, decision_model: object, step_executor_configuration: object) -> ModelSelection | None:
    if not isinstance(step_executor_configuration, UnsetType):
        from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration

        if not isinstance(step_executor_configuration, StepExecutorConfiguration):
            raise TypeError("step_executor_configuration must be StepExecutorConfiguration or UNSET")
    return model_selection(execution_model, decision_model, step_executor_configuration)


@contextmanager
def run(
    step_executor: StepExecutor | UnsetType = UNSET,
    *,
    execution_model: str | Model | None | UnsetType = UNSET,
    decision_model: str | Model | None | UnsetType = UNSET,
    step_executor_configuration: StepExecutorConfiguration | UnsetType = UNSET,
    run_id: str | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
) -> Generator[Scope]:
    """Start a fresh run, constructing a Pydantic AI executor when none is supplied.

    Supplied executors own selection and resource lifetime. Model clients are constructed only when needed.
    """
    selection = _model_selection(execution_model, decision_model, step_executor_configuration)
    if isinstance(step_executor, UnsetType):
        from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor

        step_executor = AgentStepExecutor()
    with scoping.run(step_executor, model_selection=selection, run_id=run_id, usage_meter=usage_meter) as selected:
        yield selected


@contextmanager
def scope(
    *,
    execution_model: str | Model | None | UnsetType = UNSET,
    decision_model: str | Model | None | UnsetType = UNSET,
    step_executor_configuration: StepExecutorConfiguration | UnsetType = UNSET,
    step_executor: StepExecutor | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
    oversight: Oversight | None | UnsetType = UNSET,
    lifecycle: StepLifecycle | None | UnsetType = UNSET,
    task_references: Mapping[str, object] | Merge[object] | UnsetType = UNSET,
    context_builder: ContextBuilder | UnsetType = UNSET,
    system_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    user_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    tools: Sequence[ToolEntry] | Extend[ToolEntry] | UnsetType = UNSET,
    capabilities: Sequence[AbstractCapability[StepContext]] | Extend[AbstractCapability[StepContext]] | UnsetType = UNSET,
) -> Generator[Scope]:
    """Select nested execution and model presentation, restoring both on exit.

    Omission inherits, values replace, None clears hooks, and explicit collections can be empty.
    Model selection is always resolved by the active executor, including host mediation.
    """
    selection = _model_selection(execution_model, decision_model, step_executor_configuration)
    with scoping.scope(
        step_executor=step_executor,
        model_selection=selection,
        usage_meter=usage_meter,
        oversight=oversight,
        lifecycle=lifecycle,
        task_references=task_references,
    ) as selected:
        if all(
            isinstance(value, UnsetType)
            for value in (context_builder, system_prompt_suffix_fragments, user_prompt_suffix_fragments, tools, capabilities)
        ):
            yield selected
            return
        from nighthawk.adapters.pydantic_ai.scoping import model_scope

        with model_scope(
            context_builder=context_builder,
            system_prompt_suffix_fragments=system_prompt_suffix_fragments,
            user_prompt_suffix_fragments=user_prompt_suffix_fragments,
            tools=tools,
            capabilities=capabilities,
        ):
            yield selected
