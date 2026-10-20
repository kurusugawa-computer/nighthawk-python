"""Observe tool argument failures without duplicating Pydantic AI validation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, SkipToolValidation, ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tool_manager import _ValidationDeferral
from pydantic_ai.tools import ToolDefinition

from ..runtime.operations import OperationExecution, execute_operation_async, new_operation_request
from ..runtime.step_context import StepContext

_recorded_admission_errors: ContextVar[list[BaseException] | None] = ContextVar("nighthawk_recorded_admission_errors", default=None)


class ToolAdmissionCapability(AbstractCapability[StepContext]):
    """Retain failed admissions before the toolset body boundary.

    Successful validation stays in Pydantic AI; the toolset wrapper owns the
    subsequent inspected operation. A failed validation is recorded separately
    before Pydantic AI offers retry or capability recovery.
    """

    async def wrap_tool_validate(
        self,
        ctx: RunContext[StepContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
        handler: Callable[[str | dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        # These keyword names belong to Pydantic AI's capability interface.
        if ctx.deps.operation_failure is not None:
            raise ctx.deps.operation_failure
        recorded_errors: list[BaseException] = []
        token = _recorded_admission_errors.set(recorded_errors)
        try:
            return await handler(args)
        except (ApprovalRequired, CallDeferred, SkipToolValidation, _ValidationDeferral):
            raise
        except BaseException as exception:
            if any(exception is recorded for recorded in recorded_errors) or ctx.deps.operation_failure is exception:
                raise
            await self._record_failure(ctx, call=call, tool_def=tool_def, args=args, error=exception)
            raise AssertionError("A failed admission must propagate its original exception") from exception
        finally:
            _recorded_admission_errors.reset(token)

    async def on_tool_validate_error(
        self,
        ctx: RunContext[StepContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
        error: ValidationError | ModelRetry,
    ) -> dict[str, Any]:
        # Pydantic AI offers recovery inside wrap_tool_validate. Record before application recovery hooks.
        await self._record_failure(ctx, call=call, tool_def=tool_def, args=args, error=error)
        raise AssertionError("A failed admission must propagate its original exception") from error

    async def _record_failure(
        self,
        ctx: RunContext[StepContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
        error: BaseException,
    ) -> None:
        recorded = _recorded_admission_errors.get()
        if recorded is not None:
            recorded.append(error)
        operation_kind = (tool_def.metadata or {}).get("nighthawk.operation_kind")
        expression = args.get("expression") if isinstance(args, dict) else None
        target_path = args.get("target_path") if isinstance(args, dict) else None
        request = new_operation_request(
            ctx.deps,
            kind=operation_kind if operation_kind in ("evaluation", "assignment") else "native_tool",
            tool_name=call.tool_name,
            argument_name_to_value=args if isinstance(args, dict) else {"unvalidated_arguments": args},
            expression=expression if isinstance(expression, str) and operation_kind is not None else None,
            target_path=target_path if isinstance(target_path, str) and operation_kind == "assignment" else None,
        )

        def reject_admission() -> None:
            raise error

        async def unreachable(execution: OperationExecution) -> object:
            raise AssertionError("A failed admission cannot execute a tool")

        await execute_operation_async(
            ctx.deps,
            request,
            admit=reject_admission,
            execute=unreachable,
            recoverable_admission_error=error if isinstance(error, (ValidationError, ModelRetry, ToolFailed)) else None,
        )


def with_tool_admission(capability: AbstractCapability[StepContext] | None) -> AbstractCapability[StepContext]:
    """Install the same boundary on standalone and managed backend dispatch."""
    if isinstance(capability, ToolAdmissionCapability):
        return capability
    if isinstance(capability, CombinedCapability) and any(isinstance(item, ToolAdmissionCapability) for item in capability.capabilities):
        return capability
    if capability is None:
        return ToolAdmissionCapability()
    return CombinedCapability([capability, ToolAdmissionCapability()])
