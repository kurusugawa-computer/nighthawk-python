"""Construction observations over the library executor and explicit test call boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import nighthawk as nh
from nighthawk.agent import (
    CapabilityRejectedError,
    Grant,
    RunContract,
    RunHandle,
    RunRejectedError,
    ScaffoldComposition,
)
from nighthawk.agent._executor import _LazyExecutor
from nighthawk.agent._host import current_host
from nighthawk.agent.runtime import AsyncScaffoldComposition, Runner


class LazyReportingExecutor(_LazyExecutor):
    """Count construction while testing the library's actual mediation path."""

    def __init__(self, factory: Callable[[], nh.AgentStepExecutor]) -> None:
        self.construction_count = 0

        def observed_factory() -> nh.AgentStepExecutor:
            executor = factory()
            self.construction_count += 1
            return executor

        super().__init__(observed_factory)


class NaturalCallBoundary:
    """Test-only cell context with explicitly registered callable/result contracts.

    This is not source adoption or discovery. Source experiments supply their
    separately observed identities to this invocation boundary.
    """

    def __init__(self, runner: Runner, function_to_result_type: Mapping[Callable[..., Any], type], *, grant: Grant = Grant.EMPTY) -> None:
        self.runner = runner
        self.function_to_result_type = dict(function_to_result_type)
        self.grant = grant
        self.handle_list: list[RunHandle] = []

    def _result_type(self, function: Callable[..., Any]) -> type:
        result_type = self.function_to_result_type.get(function)
        if result_type is None:
            raise RunRejectedError(reason="invalid_declaration", detail="Natural function has no registered source/result contract")
        return result_type

    def _inside_run(self, function: Callable[..., Any]) -> bool:
        host = current_host()
        if host is None:
            return False
        context = host.current_context()
        if host.ended or context is None or not host.is_node_live(context.node):
            raise CapabilityRejectedError(capability_name="function_call", reason_code="run_ended")
        if host.terminated:
            raise CapabilityRejectedError(capability_name="function_call", reason_code="run_terminated")
        if context.generated_source is None:
            self._result_type(function)
        return True

    def invoke(self, call: nh.FunctionCall, proceed: Callable[[], Any]) -> Any:
        function = call.definition.function
        if self._inside_run(function):
            return proceed()
        result_type = self._result_type(function)
        handle = RunHandle()
        self.handle_list.append(handle)
        return self.runner.run(
            ScaffoldComposition(
                RunContract(type(None), result_type),
                lambda value: proceed(),
                run_entry_grant=self.grant,
                function_definition_tuple=(function,),
            ),
            None,
            handle=handle,
        )

    async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[Any]]) -> Any:
        function = call.definition.function
        if self._inside_run(function):
            return await proceed()
        result_type = self._result_type(function)
        handle = RunHandle()
        self.handle_list.append(handle)

        async def composition_body(value: None) -> Any:
            return await proceed()

        return await self.runner.run(
            AsyncScaffoldComposition(
                RunContract(type(None), result_type),
                composition_body,
                run_entry_grant=self.grant,
                function_definition_tuple=(function,),
            ),
            None,
            handle=handle,
        )
