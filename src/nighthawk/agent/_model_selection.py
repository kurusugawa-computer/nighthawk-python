"""Fixed Runner model pairs with lazy delegates and host request reporting."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import nighthawk as nh
from nighthawk.configuration import ModelSelection

from ._executor import _LazyExecutor

type _ModelPair = tuple[str, str | None]


def _model_pair_of(configuration: nh.StepExecutorConfiguration) -> _ModelPair:
    execution_model = configuration.execution_model
    decision_model = configuration.decision_model
    if not isinstance(execution_model, str) or not (decision_model is None or isinstance(decision_model, str)):
        raise ValueError("Runner selects an execution model name and an optional decision model name")
    return execution_model, decision_model


class _ModelExecutor:
    admits_executor_steps = True

    def __init__(
        self,
        configurations: tuple[nh.StepExecutorConfiguration, ...],
        default_model_pair: _ModelPair,
        factory: Callable[[nh.StepExecutorConfiguration], nh.AgentStepExecutor],
    ) -> None:
        model_pair_to_configuration: dict[_ModelPair, nh.StepExecutorConfiguration] = {}
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
        selected_configuration = selection.apply(self._model_pair_to_configuration[self._model_pair])
        model_pair = _model_pair_of(selected_configuration)
        permitted = self._model_pair_to_configuration.get(model_pair)
        if permitted is None:
            raise ValueError("the selected model pair is not configured on Runner")
        if isinstance(selection.configuration, nh.StepExecutorConfiguration) and selection.configuration != permitted:
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
