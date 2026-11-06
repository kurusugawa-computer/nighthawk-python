from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from nighthawk.adapters.pydantic_ai._harness_executor import _LazyExecutor
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration, selected_configuration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.core.model_selection import ModelSelection
from nighthawk.harness.audit import PerimeterAuditStore
from nighthawk.harness.confinement import PerimeterConfinement
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import ResourceAllocation
from nighthawk.harness.run_contract import RunIdentity
from nighthawk.harness.runner import Runner
from nighthawk.harness.termination import TerminationMechanism, unavailable_termination_mechanism

type _ModelPair = tuple[str, str | None]


def _model_pair_of(configuration: StepExecutorConfiguration) -> _ModelPair:
    execution_model = configuration.execution_model
    decision_model = configuration.decision_model
    if not isinstance(execution_model, str) or not (decision_model is None or isinstance(decision_model, str)):
        raise ValueError("Runner selects an execution model name and an optional decision model name")
    return execution_model, decision_model


class _ModelExecutor:
    admits_executor_steps = True

    def __init__(
        self,
        configurations: tuple[StepExecutorConfiguration, ...],
        default_model_pair: _ModelPair,
        factory: Callable[[StepExecutorConfiguration], AgentStepExecutor],
    ) -> None:
        model_pair_to_configuration: dict[_ModelPair, StepExecutorConfiguration] = {}
        for configuration in configurations:
            model_pair = _model_pair_of(configuration)
            if model_pair in model_pair_to_configuration:
                raise ValueError("configured model pairs must be distinct")
            model_pair_to_configuration[model_pair] = configuration.model_copy(deep=True)
        if default_model_pair not in model_pair_to_configuration:
            raise ValueError("the default model pair must be configured")
        self._model_pair_to_configuration = MappingProxyType(model_pair_to_configuration)
        self._model_pair_to_executor = MappingProxyType(
            {
                model_pair: _LazyExecutor(lambda configuration=configuration: factory(configuration.model_copy(deep=True)))
                for model_pair, configuration in model_pair_to_configuration.items()
            }
        )
        self._model_pair = default_model_pair

    def resolve_model_selection(self, selection: ModelSelection) -> _ModelExecutor:
        configuration = selected_configuration(selection, self._model_pair_to_configuration[self._model_pair])
        model_pair = _model_pair_of(configuration)
        permitted = self._model_pair_to_configuration.get(model_pair)
        if permitted is None:
            raise ValueError("the selected model pair is not configured on Runner")
        if isinstance(selection.configuration, StepExecutorConfiguration) and selection.configuration != permitted:
            raise ValueError("a configuration selection must equal one configured on Runner")
        selected = object.__new__(_ModelExecutor)
        selected._model_pair_to_configuration = self._model_pair_to_configuration
        selected._model_pair_to_executor = self._model_pair_to_executor
        selected._model_pair = model_pair
        return selected

    def run_step(self, **arguments: Any) -> Any:
        return self._model_pair_to_executor[self._model_pair].run_step(**arguments)

    async def run_step_async(self, **arguments: Any) -> Any:
        return await self._model_pair_to_executor[self._model_pair].run_step_async(**arguments)


def model_runner(
    *,
    model_configuration_tuple: tuple[StepExecutorConfiguration, ...],
    default_execution_model: str,
    default_decision_model: str | None = None,
    perimeter_policy: PerimeterPolicy,
    resource_allocation_factory: Callable[[RunIdentity], ResourceAllocation],
    perimeter_confinement: PerimeterConfinement | None = None,
    perimeter_audit_store: PerimeterAuditStore | None = None,
    termination_mechanism: TerminationMechanism | None = None,
    executor_factory: Callable[[StepExecutorConfiguration], AgentStepExecutor] = AgentStepExecutor,
) -> Runner:
    """Permit a fixed set of execution and decision model pairs without constructing providers.

    Each configuration is identified by its ``execution_model`` name and optional ``decision_model`` name.
    Lexical ``nh.scope(execution_model=..., decision_model=...)`` replaces either model of the current pair,
    and the resulting pair must be configured; its configured settings apply.
    A full ``step_executor_configuration`` selection must equal a configured one.
    Without a decision model, ``decide`` blocks fail. Every selection retains the same host, grant, and run allocation.
    ``executor_factory`` is trusted provider construction, invoked lazily once per selected pair
    and again only for an event loop that runs steps while every existing delegate's loop is still running.
    """
    return Runner(
        step_executor=_ModelExecutor(model_configuration_tuple, (default_execution_model, default_decision_model), executor_factory),
        perimeter_policy=perimeter_policy,
        resource_allocation_factory=resource_allocation_factory,
        perimeter_confinement=perimeter_confinement,
        perimeter_audit_store=perimeter_audit_store,
        termination_mechanism=termination_mechanism if termination_mechanism is not None else unavailable_termination_mechanism(),
    )
