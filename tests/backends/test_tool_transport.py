from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest
from pydantic import BeforeValidator, TypeAdapter, ValidationError
from pydantic_ai import Agent, RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends.codex import CodexModel
from nighthawk.backends.tool_bridge import build_tool_name_to_handler
from nighthawk.backends.tool_transport import ToolTransport, wrap_transported_tool
from nighthawk.tools.contracts import ToolHandlerResult
from nighthawk.tools.declarations import get_visible_tools
from tests.support.codex import _write_executable_codex_stub
from tests.support.contexts import new_context
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("failure", [RuntimeError("application failed"), ApprovalRequired(), CallDeferred()])
def test_transport_stops_provider_that_swallows_handler_failure(failure: Exception) -> None:
    effects: list[str] = []

    async def execute() -> None:
        async def handler(argument_name_to_value: dict[str, Any]) -> ToolHandlerResult:
            effects.append("body")
            raise failure

        async def request() -> str:
            wrapped = wrap_transported_tool(handler)
            try:
                with suppress(Exception):
                    await wrapped({})
                with suppress(Exception):
                    await wrapped({})
                await asyncio.Event().wait()
            finally:
                effects.append("provider stopped")
            return "unreachable"

        with pytest.raises(type(failure)) as caught:
            await asyncio.wait_for(ToolTransport().run(request), timeout=2)
        assert caught.value is failure

    asyncio.run(execute())
    assert effects == ["body", "provider stopped"]


def test_transport_returns_mcp_thread_calls_to_owner_loop_and_context() -> None:
    marker = ContextVar("transport_test_marker", default="missing")

    async def execute() -> None:
        owner_loop = asyncio.get_running_loop()
        future = owner_loop.create_future()
        marker.set("Natural caller")

        async def handler(argument_name_to_value: dict[str, Any]) -> ToolHandlerResult:
            assert asyncio.get_running_loop() is owner_loop
            assert marker.get() == "Natural caller"
            owner_loop.call_soon(future.set_result, 17)
            return {"kind": "tool_result", "tool_outcome": {"payload": await future, "error": None}}

        async def request() -> ToolHandlerResult:
            wrapped = wrap_transported_tool(handler)

            def remote_thread() -> ToolHandlerResult:
                marker.set("transport thread")

                async def call() -> ToolHandlerResult:
                    return await wrapped({})

                return asyncio.run(call())

            return await asyncio.to_thread(remote_thread)

        result = await asyncio.wait_for(ToolTransport().run(request), timeout=2)
        assert result["kind"] == "tool_result"
        assert result["tool_outcome"]["payload"] == 17

    asyncio.run(execute())


def test_transport_rejects_late_callback_after_owner_loop_closes() -> None:
    async def handler(argument_name_to_value: dict[str, Any]) -> ToolHandlerResult:
        raise AssertionError("a late callback must not run")

    async def execute() -> Any:
        async def request() -> Any:
            return wrap_transported_tool(handler)

        return await ToolTransport().run(request)

    callback = asyncio.run(execute())
    with pytest.raises(RuntimeError, match="request has finished"):
        asyncio.run(callback({}))


def test_caller_cancellation_cancels_transport_and_pending_handler() -> None:
    effects: list[str] = []

    async def execute() -> None:
        entered = asyncio.Event()

        async def handler(argument_name_to_value: dict[str, Any]) -> ToolHandlerResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                effects.append("handler stopped")
            raise AssertionError("unreachable")

        async def request() -> None:
            try:
                await wrap_transported_tool(handler)({})
            finally:
                effects.append("provider stopped")

        task = asyncio.create_task(ToolTransport().run(request))
        await entered.wait()
        task.cancel("caller cancelled")
        with pytest.raises(asyncio.CancelledError, match="caller cancelled"):
            await task

    asyncio.run(execute())
    assert effects == ["handler stopped", "provider stopped"]


def test_backend_schema_validator_exception_is_not_a_retry() -> None:
    failure = TypeError("application validator failed")

    def reject(value: object) -> object:
        raise failure

    async def action(value: int) -> int:
        raise AssertionError("body must not run")

    from typing import Annotated

    action.__annotations__["value"] = Annotated[int, BeforeValidator(reject)]

    async def execute() -> None:
        context = RunContext(deps=new_context(), model=TestModel(), usage=RunUsage())
        toolset = FunctionToolset([action])
        tool_name_to_tool = await toolset.get_tools(context)
        with set_current_run_context(context):
            tool_name_to_handler = await build_tool_name_to_handler(
                model_request_parameters=ModelRequestParameters(function_tools=[tool_name_to_tool["action"].tool_def]),
                visible_tools=[action],
            )
            with pytest.raises(TypeError) as caught:
                await tool_name_to_handler["action"]({"value": 1})
        assert caught.value is failure

    asyncio.run(execute())


def test_backend_runs_pydantic_ai_async_argument_validator_once() -> None:
    effects: list[str] = []

    async def action(value: int) -> int:
        effects.append("body")
        return value

    async def validate(context: RunContext[nh.StepContext], *, value: int) -> None:
        await asyncio.sleep(0)
        effects.append("validate")
        if value < 0:
            raise ModelRetry("Use a nonnegative value")

    tool = Tool[nh.StepContext](action, args_validator=validate)

    async def execute() -> None:
        context = RunContext(deps=new_context(), model=TestModel(), usage=RunUsage())
        toolset = FunctionToolset([tool])
        tool_name_to_tool = await toolset.get_tools(context)
        with set_current_run_context(context):
            tool_name_to_handler = await build_tool_name_to_handler(
                model_request_parameters=ModelRequestParameters(function_tools=[tool_name_to_tool["action"].tool_def]),
                visible_tools=[tool],
            )
            retry = await tool_name_to_handler["action"]({"value": -1})
            assert retry["kind"] == "retry_prompt"
            assert "nonnegative" in retry["retry_text"]
            success = await tool_name_to_handler["action"]({"value": 3})
            assert success["kind"] == "tool_result"
            assert success["tool_outcome"]["payload"] == 3

    asyncio.run(execute())
    assert effects == ["validate", "validate", "body"]


def test_backend_respects_pydantic_ai_tool_approval_requirement() -> None:
    async def action() -> int:
        raise AssertionError("unapproved body must not run")

    tool = Tool(action, requires_approval=True)

    async def execute() -> None:
        context = RunContext(deps=new_context(), model=TestModel(), usage=RunUsage())
        tool_name_to_tool = await FunctionToolset([tool]).get_tools(context)
        with set_current_run_context(context):
            tool_name_to_handler = await build_tool_name_to_handler(
                model_request_parameters=ModelRequestParameters(function_tools=[tool_name_to_tool["action"].tool_def]),
                visible_tools=[tool],
            )
            with pytest.raises(ApprovalRequired):
                await tool_name_to_handler["action"]({})

    asyncio.run(execute())


def test_backend_native_body_validation_error_propagates_without_retry() -> None:
    calls: list[str] = []

    async def action() -> int:
        calls.append("body")
        return TypeAdapter(int).validate_python("invalid application data")

    async def execute() -> None:
        context = RunContext(deps=new_context(), model=TestModel(), usage=RunUsage())
        toolset = FunctionToolset([action])

        async def before(context: Any, *, call: Any, tool_def: Any, args: Any) -> Any:
            calls.append("hook")
            return args

        context.tool_manager = ToolManager(toolset=toolset, root_capability=Hooks(before_tool_execute=before))
        tool_name_to_tool = await toolset.get_tools(context)
        with set_current_run_context(context):
            tool_name_to_handler = await build_tool_name_to_handler(
                model_request_parameters=ModelRequestParameters(function_tools=[tool_name_to_tool["action"].tool_def]),
                visible_tools=[action],
            )
            with pytest.raises(ValidationError) as caught:
                await tool_name_to_handler["action"]({})
        assert context.deps.operation_failure is caught.value

    asyncio.run(execute())
    assert calls == ["hook", "body"]


def test_explicit_native_model_retry_does_not_abort_transport() -> None:
    calls: list[str] = []

    async def action() -> int:
        calls.append("body")
        raise ModelRetry("Try the documented alternative")

    async def execute() -> None:
        context = RunContext(deps=new_context(), model=TestModel(), usage=RunUsage())
        tool_name_to_tool = await FunctionToolset([action]).get_tools(context)

        async def request() -> ToolHandlerResult:
            with set_current_run_context(context):
                tool_name_to_handler = await build_tool_name_to_handler(
                    model_request_parameters=ModelRequestParameters(function_tools=[tool_name_to_tool["action"].tool_def]),
                    visible_tools=[action],
                )
                return await tool_name_to_handler["action"]({})

        result = await ToolTransport().run(request)
        assert result == {"kind": "retry_prompt", "retry_text": "Try the documented alternative"}
        assert context.deps.operation_failure is None

    asyncio.run(execute())
    assert calls == ["body"]


@pytest.mark.parametrize("fail", [False, True])
def test_codex_mcp_executes_on_owner_loop_and_preserves_original_failure(tmp_path: Path, fail: bool) -> None:
    executable = _write_executable_codex_stub(directory=tmp_path)
    executable.write_text(executable.read_text().replace('"expression": "1 + 1"', '"expression": "application()"'))
    failure = ValueError("application failed over MCP")
    calls: list[str] = []

    async def execute() -> None:
        owner_loop = asyncio.get_running_loop()
        future = owner_loop.create_future()

        async def application() -> int:
            assert asyncio.get_running_loop() is owner_loop
            calls.append("application")
            if fail:
                raise failure
            owner_loop.call_soon(future.set_result, 2)
            return await future

        context = new_context(application=application)
        agent = Agent(model=CodexModel(), deps_type=nh.StepContext, output_type=str)
        from typing import cast

        settings = cast(ModelSettings, {"executable": str(executable), "allowed_tool_names": ("nh_evaluate",), "working_directory": str(tmp_path)})
        with nh.run(StubExecutor()):
            tools = FunctionToolset(get_visible_tools())
            if fail:
                with pytest.raises(ValueError) as caught:
                    await asyncio.wait_for(agent.run("Call application.", deps=context, toolsets=[tools], model_settings=settings), timeout=10)
                assert caught.value is context.operation_failure is failure
            else:
                result = await asyncio.wait_for(agent.run("Call application.", deps=context, toolsets=[tools], model_settings=settings), timeout=10)
                assert "nh_evaluate_text" in result.output

    asyncio.run(execute())
    assert calls == ["application"]
