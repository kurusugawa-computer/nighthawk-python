"""Keep Pydantic AI argument validation inside ordered model-tool dispatch.

Pydantic AI 2.37 validates function batches before its execution barriers.
These scoped compatibility hooks delegate validation, execution, retries,
deferrals, and output selection to Pydantic AI; they only move validation to
the corresponding sequential dispatch. Ordinary Pydantic AI runs are unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic_ai import _tool_execution
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import FunctionToolCallEvent, HandleResponseEvent, ToolCallPart
from pydantic_ai.tool_manager import ToolManager, ValidatedToolCall
from pydantic_ai.tools import ToolApproved

from ..runtime.step_context import StepContext

_ordered_dispatch: ContextVar[bool] = ContextVar("nighthawk_ordered_model_dispatch", default=False)
_original_function_calls = _tool_execution._ToolCallProcessor._run_function_calls
_original_validation = _tool_execution._ExhaustiveProcessor._validate_function_calls
_original_call_tool = _tool_execution._ExhaustiveProcessor._call_tool


def _requires_ordered_validation(processor: Any) -> bool:
    context = processor.tool_manager.ctx
    return _ordered_dispatch.get() and context is not None and isinstance(context.deps, StepContext)


async def _run_function_calls(self: Any, calls: list[ToolCallPart]) -> AsyncIterator[HandleResponseEvent]:
    batches = ([call] for call in calls) if _requires_ordered_validation(self) else (calls,)
    for batch in batches:
        async for event in _original_function_calls(self, batch):
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
    return await _original_call_tool(self, tool_call, tool_call_result=tool_call_result)


# Install dispatchers once under Python's import lock. Context variables scope
# their behavior across asynchronous tasks and supported thread bridges.
_tool_execution._ToolCallProcessor._run_function_calls = _run_function_calls
_tool_execution._ExhaustiveProcessor._validate_function_calls = _validate_exhaustive_calls
_tool_execution._ExhaustiveProcessor._call_tool = _call_exhaustive_tool


@contextmanager
def sequential_model_tools() -> Iterator[None]:
    token = _ordered_dispatch.set(True)
    try:
        with ToolManager.parallel_execution_mode("sequential"):
            yield
    finally:
        _ordered_dispatch.reset(token)
