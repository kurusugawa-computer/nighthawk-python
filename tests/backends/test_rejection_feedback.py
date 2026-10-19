from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.types import TextContent
from opentelemetry import context as otel_context
from pydantic_ai import RunContext, Tool
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends.mcp_boundary import call_tool_for_claude_code_sdk, call_tool_for_low_level_mcp_server
from nighthawk.backends.tool_bridge import build_tool_name_to_handler
from nighthawk.backends.tool_transport import ToolTransport
from nighthawk.lifecycle import StepLifecycle
from nighthawk.oversight import Accept, Oversight, RejectOperation
from nighthawk.runtime import OperationFinished, OperationRequest
from nighthawk.runtime.step_context import ToolResultRenderingPolicy
from tests.support.contexts import new_context
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("transport", ["handler", "mcp", "sdk"])
@pytest.mark.parametrize("replace_result", ["none", "success", "retry"])
def test_transported_refusal_keeps_feedback_and_allows_correction(transport: str, replace_result: str) -> None:
    bodies: list[int] = []
    operations: list[OperationFinished] = []
    reason = "Host refuses value 1. " * 30

    def action(value: int, secret: str) -> object:
        bodies.append(value)
        return {"error": {"status": "rejected"}, "value": value}

    async def inspect(request: OperationRequest) -> Accept | RejectOperation:
        await asyncio.sleep(0)
        if request.argument_name_to_value["value"] == 1:
            return RejectOperation(reason, "Use value 2.")
        return Accept()

    async def replace(context: Any, *, call: Any, tool_def: Any, args: Any, result: Any) -> object:
        if args["value"] == 1:
            if replace_result == "success":
                return 42
            if replace_result == "retry":
                raise ModelRetry("A different explanation")
        return result

    async def execute() -> None:
        context = new_context()
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        tools = [Tool(action)]
        toolset = FunctionToolset(tools)
        tool_name_to_tool = await toolset.get_tools(run_context)
        run_context.tool_manager = ToolManager(
            toolset=toolset, ctx=run_context, tools=tool_name_to_tool, root_capability=Hooks(after_tool_execute=replace)
        )
        parameters = ModelRequestParameters(function_tools=[tool.tool_def for tool in tool_name_to_tool.values()])

        async def provider_request() -> None:
            with set_current_run_context(run_context):
                handlers = await build_tool_name_to_handler(model_request_parameters=parameters, visible_tools=tools)
                handler = handlers["action"]
                for value in (1, 2):
                    arguments = {"value": value, "secret": "private-host-value"}
                    if transport == "handler":
                        result = await handler(arguments)
                        assert result["kind"] == "tool_result"
                        error = result["tool_outcome"]["error"]
                        is_error = error is not None
                    elif transport == "mcp":
                        result = await call_tool_for_low_level_mcp_server(
                            tool_name="action",
                            arguments=arguments,
                            tool_handler=handler,
                            parent_otel_context=otel_context.get_current(),
                            rendering_policy=ToolResultRenderingPolicy("o200k_base", 4, "default"),
                        )
                        is_error = result.is_error
                        error = result.structured_content["error"] if result.structured_content is not None else None
                        if value == 1:
                            content = result.content[0]
                            assert isinstance(content, TextContent)
                            assert json.loads(content.text)["error"]["message"] == reason
                    else:
                        result = await call_tool_for_claude_code_sdk(
                            tool_name="action",
                            arguments=arguments,
                            tool_handler=handler,
                            parent_otel_context=otel_context.get_current(),
                            rendering_policy=ToolResultRenderingPolicy("o200k_base", 4, "default"),
                        )
                        is_error = result.get("isError", False)
                        structured_content = result.get("structuredContent")
                        assert structured_content is None or isinstance(structured_content, dict)
                        error = structured_content["error"] if structured_content is not None else None
                        if value == 1:
                            content = result["content"]
                            assert isinstance(content, list)
                            assert json.loads(content[0]["text"])["error"]["message"] == reason
                    assert is_error is (value == 1)
                    if value == 1:
                        assert isinstance(error, dict)
                        assert error.get("status") == "rejected"
                        assert error.get("message") == reason and error.get("guidance") == "Use value 2."
                        assert error.get("operation_id") == operations[0].request.operation_id
                        assert error.get("evaluation_started") is False
                        assert "private-host-value" not in str(error)

        await ToolTransport().run(provider_request)

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        asyncio.run(execute())
    assert bodies == [2]
    assert [operation.status for operation in operations] == ["rejected", "succeeded"]
