"""Keep validation and refusal barriers inside ordered model-tool dispatch.

Pydantic AI 2.37 validates function batches before its execution barriers.
These scoped compatibility hooks move validation to sequential dispatch,
stop later calls after a refusal, and discard candidates from that response.
Pydantic AI still owns execution, retry limits, and output selection among
permitted candidates. Ordinary Pydantic AI runs are unchanged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic_ai import _agent_graph, _tool_execution
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, HandleResponseEvent, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.tool_manager import ToolManager, ValidatedToolCall
from pydantic_ai.tools import ToolApproved

from ..runtime.model_response import get_response_rejection, model_response_scope
from ..runtime.operations import OperationExecution, execute_operation_async, new_operation_request
from ..runtime.step_context import StepContext

_ordered_dispatch: ContextVar[bool] = ContextVar("nighthawk_ordered_model_dispatch", default=False)
_original_function_calls = _tool_execution._ToolCallProcessor._run_function_calls
_original_validation = _tool_execution._ExhaustiveProcessor._validate_function_calls
_original_call_tool = _tool_execution._ExhaustiveProcessor._call_tool
_original_run = _tool_execution._ToolCallProcessor.run
_original_output_call = _tool_execution._ToolCallProcessor._run_output_tool_call
_original_run_output = _tool_execution._ToolCallProcessor._run_output
_original_finalize_deferred = _tool_execution._ToolCallProcessor._finalize_deferred
_original_early_strategy = _tool_execution._EarlyProcessor._run_strategy
_original_handle_tool_calls = _agent_graph.CallToolsNode._handle_tool_calls


def _requires_ordered_validation(processor: Any) -> bool:
    context = processor.tool_manager.ctx
    return _ordered_dispatch.get() and context is not None and isinstance(context.deps, StepContext)


def _rejection(processor: Any) -> Any:
    return get_response_rejection(processor.tool_manager.ctx.deps) if _requires_ordered_validation(processor) else None


async def _skip_call(processor: Any, call: ToolCallPart, *, output: bool = False) -> ToolReturnPart:
    context = processor.tool_manager.ctx.deps
    rejection = get_response_rejection(context)
    assert rejection is not None
    tool_definition = processor.tool_manager.get_tool_def(call.tool_name)
    kind = (tool_definition.metadata or {}).get("nighthawk.operation_kind") if tool_definition is not None else None
    arguments = call.args if isinstance(call.args, dict) else {"unvalidated_arguments": call.args}
    expression = arguments.get("expression")
    target_path = arguments.get("target_path")
    request = new_operation_request(
        context,
        kind="output" if output else kind if kind in ("evaluation", "assignment", "inspection") else "native_tool",
        tool_name=call.tool_name,
        argument_name_to_value=arguments,
        expression=expression if isinstance(expression, str) else None,
        target_path=target_path if isinstance(target_path, str) else None,
    )

    async def unreachable(execution: OperationExecution) -> object:
        raise AssertionError("A skipped operation cannot execute")

    await execute_operation_async(context, request, admit=lambda: None, execute=unreachable, blocked_by=rejection)
    return ToolReturnPart(
        tool_name=call.tool_name,
        tool_call_id=call.tool_call_id,
        outcome="failed",
        content={
            "status": "skipped",
            "operation_id": request.operation_id,
            "step_execution_id": request.execution_reference.step_execution_id,
            "blocked_by_operation_id": rejection.request.operation_id,
            "evaluation_started": False,
            "message": "Not executed after an operation rejection. Choose the next operations in a new response.",
        },
    )


async def _run(self: Any) -> AsyncIterator[HandleResponseEvent]:
    if not _requires_ordered_validation(self):
        async for event in _original_run(self):
            yield event
        return
    with model_response_scope(self.tool_manager.ctx.deps, reuse=True) as execution:
        if execution.rejection is not None:
            # Native output under `early` was inspected before tool dispatch.
            # Pydantic AI suppresses its retry; preserve both feedback and accounting.
            self.ctx.state.consume_output_retry(self.ctx.deps.max_output_retries)
            self.output_parts.append(RetryPromptPart(content=json.dumps(execution.rejection.model_feedback())))
        async for event in _original_run(self):
            yield event
        if execution.rejection is not None:
            # An earlier accepted output cannot mask a later refusal in the same response.
            self.final_result = None
            if self.winning_output_part is not None:
                self.winning_output_part.content = {
                    "status": "discarded",
                    "blocked_by_operation_id": execution.rejection.request.operation_id,
                }


async def _handle_tool_calls(
    self: Any, ctx: Any, tool_calls: list[ToolCallPart], *, response_output: Any = None
) -> AsyncIterator[HandleResponseEvent]:
    if _ordered_dispatch.get() and isinstance(ctx.deps.user_deps, StepContext):
        with model_response_scope(ctx.deps.user_deps):
            async for event in _original_handle_tool_calls(self, ctx, tool_calls, response_output=response_output):
                yield event
    else:
        async for event in _original_handle_tool_calls(self, ctx, tool_calls, response_output=response_output):
            yield event


async def _run_function_calls(self: Any, calls: list[ToolCallPart]) -> AsyncIterator[HandleResponseEvent]:
    batches = ([call] for call in calls) if _requires_ordered_validation(self) else (calls,)
    for batch in batches:
        if _rejection(self) is not None:
            for call in batch:
                part = await _skip_call(self, call)
                self.output_parts.append(part)
                yield FunctionToolCallEvent(call, args_valid=None)
                yield FunctionToolResultEvent(part)
            continue
        async for event in _original_function_calls(self, batch):
            yield event


async def _call_tool(self: Any, tool_call: Any, *, tool_call_result: Any) -> Any:
    before = _rejection(self)
    result = await _original_call_tool(self, tool_call, tool_call_result=tool_call_result)
    if _requires_ordered_validation(self):
        context = self.tool_manager.ctx.deps
        if context.operation_failure is not None:
            raise context.operation_failure
        rejection = get_response_rejection(context)
        if before is None and rejection is not None:
            call = tool_call.call if isinstance(tool_call, ValidatedToolCall) else tool_call
            return [
                ToolReturnPart(tool_name=call.tool_name, tool_call_id=call.tool_call_id, content=rejection.model_feedback(), outcome="denied")
            ], None
    return result


async def _run_output_tool_call(self: Any, call: ToolCallPart) -> Any:
    if _rejection(self) is not None:
        part = await _skip_call(self, call, output=True)
        # No output validator or retry budget is consumed by an unattempted candidate.
        return _tool_execution._OutputCallResult(
            call=call,
            retry_part=RetryPromptPart(tool_name=call.tool_name, tool_call_id=call.tool_call_id, content=json.dumps(part.content)),
        )
    result = await _original_output_call(self, call)
    if _rejection(self) is not None and result.raise_exc is not None:
        # Exhaustive output settlement must not hide exhausted retry limits behind an earlier candidate.
        raise result.raise_exc
    return result


async def _run_output(self: Any, call: ToolCallPart) -> AsyncIterator[HandleResponseEvent]:
    if _rejection(self) is not None:
        part = await _skip_call(self, call, output=True)
        for event in self._record_output_part(call, part, args_valid=None):
            yield event
        return
    async for event in _original_run_output(self, call):
        yield event


async def _finalize_deferred(self: Any) -> AsyncIterator[HandleResponseEvent]:
    if _rejection(self) is not None:
        settled = {part.tool_call_id for part in self.output_parts if isinstance(part, (ToolReturnPart, RetryPromptPart))}
        for call in self.tool_calls:
            if call.tool_call_id not in settled:
                part = await _skip_call(self, call)
                self.output_parts.append(part)
                yield FunctionToolCallEvent(call, args_valid=None)
                yield FunctionToolResultEvent(part)
        return
    async for event in _original_finalize_deferred(self):
        yield event


async def _run_early_strategy(self: Any) -> AsyncIterator[HandleResponseEvent]:
    strategy = _tool_execution._GracefulProcessor._run_strategy if _requires_ordered_validation(self) else _original_early_strategy
    async for event in strategy(self):
        yield event


async def _validate_exhaustive_calls(
    self: Any, calls: list[ToolCallPart], *, validated_calls: dict[str, ValidatedToolCall[Any]]
) -> AsyncIterator[HandleResponseEvent]:
    if not _requires_ordered_validation(self):
        async for event in _original_validation(self, calls, validated_calls=validated_calls):
            yield event
        return
    # Exhaustive processing owns output settlement across the full batch. Keep
    # that implementation, but validate each function when its barrier runs.
    # Its upfront call event truthfully leaves args_valid unknown at this point.
    for call in calls:
        yield FunctionToolCallEvent(call, args_valid=None)


async def _call_exhaustive_tool(self: Any, tool_call: Any, *, tool_call_result: Any) -> Any:
    if _rejection(self) is not None:
        call = tool_call.call if isinstance(tool_call, ValidatedToolCall) else tool_call
        return [await _skip_call(self, call)], None
    if (
        _requires_ordered_validation(self)
        and isinstance(tool_call, ToolCallPart)
        and (tool_call_result is None or isinstance(tool_call_result, ToolApproved))
    ):
        try:
            if isinstance(tool_call_result, ToolApproved):
                metadata = self.tool_call_metadata.get(tool_call.tool_call_id) if self.tool_call_metadata else None
                tool_call = await self._validate_approved_call(tool_call, approved=tool_call_result, metadata=metadata)
            else:
                tool_call = await self.tool_manager.validate_tool_call(tool_call)
        except UnexpectedModelBehavior:
            self.ctx.state.check_incomplete_tool_call()
            raise
    return await _call_tool(self, tool_call, tool_call_result=tool_call_result)


# Install dispatchers once under Python's import lock. Context variables scope
# their behavior across asynchronous tasks and supported thread bridges.
_tool_execution._ToolCallProcessor._run_function_calls = _run_function_calls
_tool_execution._ToolCallProcessor.run = _run
_tool_execution._ToolCallProcessor._call_tool = _call_tool
_tool_execution._ToolCallProcessor._run_output_tool_call = _run_output_tool_call
_tool_execution._ToolCallProcessor._run_output = _run_output
_tool_execution._ToolCallProcessor._finalize_deferred = _finalize_deferred
_tool_execution._EarlyProcessor._run_strategy = _run_early_strategy
_tool_execution._ExhaustiveProcessor._validate_function_calls = _validate_exhaustive_calls
_tool_execution._ExhaustiveProcessor._call_tool = _call_exhaustive_tool
_agent_graph.CallToolsNode._handle_tool_calls = _handle_tool_calls


@contextmanager
def sequential_model_tools() -> Iterator[None]:
    token = _ordered_dispatch.set(True)
    try:
        with ToolManager.parallel_execution_mode("sequential"):
            yield
    finally:
        _ordered_dispatch.reset(token)
