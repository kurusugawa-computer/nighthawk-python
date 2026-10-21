"""Test utilities for Nighthawk applications.

Provides test executors and convenience factories for writing
deterministic tests of Natural functions without LLM API calls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydantic import Field

from .composition import UNSET, UnsetType
from .runtime.execution_reference import ExecutionReference
from .runtime.step_context import StepContext
from .runtime.step_definition import BlockKind
from .runtime.step_outcome import Break, Continue, OutcomeKind, Pass, Raise, Return, StepOutcome
from .runtime.step_request import StepCandidate, StepRequest


@dataclass
class StepCall:
    """Recorded information about a single Natural block execution.

    Attributes:
        block_kind: execute or decide.
        input_binding_name_to_value: Complete explicitly referenced evidence.
        execution_reference: Invocation identity shared with oversight and terminal delivery.
        natural_program: The processed Natural block text (after frontmatter removal and interpolation).
        output_binding_names: Write binding names (``<:name>`` targets) requested by the Natural function.
        binding_name_to_type: Mapping from binding name to its expected type.
            Explicitly annotated bindings carry the declared type; unannotated bindings are inferred from the initial value at runtime.
        allowed_outcome_kinds: Outcome kinds allowed for this step, determined by syntactic context and deny frontmatter.
        step_locals: Snapshot of step-local variables at the time of execution.
            Contains function parameters and local variables.
        step_globals: Shallow snapshot of module globals selected by read bindings,
            task reference names, or discovered type aliases, excluding local names.
            This selection is not the complete rendered prompt or an access allowlist.
    """

    execution_reference: ExecutionReference
    natural_program: str
    output_binding_names: list[str]
    binding_name_to_type: dict[str, object]
    allowed_outcome_kinds: tuple[OutcomeKind, ...]
    step_locals: dict[str, object]
    step_globals: dict[str, object]
    task_name_to_value: dict[str, object]
    block_kind: BlockKind = "execute"
    input_binding_name_to_value: dict[str, object] = field(default_factory=dict)


class StepResponse(StepCandidate):
    """Scripted response for a single Natural block execution.

    Attributes:
        binding_name_to_value: Complete output selection.
            Omission or UNSET selects initialized declared outputs; {} selects no writes.
            Undeclared names fail finalization. Decisions always require an explicit answer mapping.
        outcome: Resolved Python control result. Strings are values, never expressions.
    """

    outcome: StepOutcome = Field(default_factory=Pass)


def _build_step_call(
    processed_natural_program: str,
    step_context: StepContext,
    output_binding_names: list[str],
    allowed_outcome_kinds: tuple[OutcomeKind, ...],
    block_kind: BlockKind,
    input_binding_name_to_value: Mapping[str, object],
) -> StepCall:
    referenced_global_names = (
        step_context.read_binding_names | (set(step_context.environment.task_name_to_value) | step_context.type_alias_reference_names)
    ) - step_context.step_locals.keys()
    global_name_to_value = {name: step_context.step_globals[name] for name in referenced_global_names if name in step_context.step_globals}
    return StepCall(
        execution_reference=step_context.execution_reference,
        natural_program=processed_natural_program,
        block_kind=block_kind,
        input_binding_name_to_value=dict(input_binding_name_to_value),
        output_binding_names=list(output_binding_names),
        binding_name_to_type=dict(step_context.binding_name_to_type),
        allowed_outcome_kinds=allowed_outcome_kinds,
        step_locals=dict(step_context.step_locals),
        step_globals=global_name_to_value,
        task_name_to_value=dict(step_context.environment.task_name_to_value),
    )


class ScriptedExecutor:
    """Test executor that returns scripted responses and records calls.

    Responses are consumed in order.
    Once exhausted, an explicitly supplied ``default_response`` is used for subsequent calls.
    Without a default, the unexpected invocation is recorded and raises AssertionError.
    Ordinary runtime execution wraps that assertion in ExecutionError with failure stage ``executor``.
    """

    def __init__(
        self,
        responses: list[StepResponse] | None = None,
        *,
        default_response: StepResponse | None = None,
    ) -> None:
        self.responses: list[StepResponse] = list(responses) if responses else []
        self.default_response: StepResponse | None = default_response
        self.calls: list[StepCall] = []

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        call = _build_step_call(
            request.processed_natural_program,
            request.step_context,
            list(request.output_binding_names),
            request.allowed_outcome_kinds,
            request.block_kind,
            request.input_binding_name_to_value,
        )
        self.calls.append(call)
        index = len(self.calls) - 1
        response = self.responses[index] if index < len(self.responses) else self.default_response
        if response is None:
            raise AssertionError(
                f"ScriptedExecutor has no response for invocation {index + 1}; "
                f"{len(self.responses)} responses were supplied. "
                "Supply another response or an explicit default_response."
            )
        return response

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        return self.run_step(request=request)


class CallbackExecutor:
    """Test executor that delegates to a user-provided callback function.

    Use when response logic depends on the Natural block input (e.g.,
    routing different binding values based on the program text).
    """

    def __init__(self, handler: Callable[[StepCall], StepResponse]) -> None:
        self.handler: Callable[[StepCall], StepResponse] = handler
        self.calls: list[StepCall] = []

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        call = _build_step_call(
            request.processed_natural_program,
            request.step_context,
            list(request.output_binding_names),
            request.allowed_outcome_kinds,
            request.block_kind,
            request.input_binding_name_to_value,
        )
        self.calls.append(call)
        response = self.handler(call)
        return response

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        return self.run_step(request=request)


def pass_response(*, binding_name_to_value: Mapping[str, object] | UnsetType = UNSET) -> StepResponse:
    """Continue with automatic output selection, or an explicit complete mapping."""
    return StepResponse(binding_name_to_value=binding_name_to_value)


def raise_response(message: str, *, error_type: type[BaseException] = Exception) -> StepResponse:
    """Request an exception constructed after commit inspection.

    Args:
        message: Message passed to the exception constructor.
        error_type: Resolved exception class, independent of the Natural namespace.

    Raises:
        TypeError: If the message or exception class is invalid.
    """
    return StepResponse(outcome=Raise(message, error_type))


def return_response(value: object, /, *, binding_name_to_value: Mapping[str, object] | UnsetType = UNSET) -> StepResponse:
    """Return a Python value subject to the Natural function's return contract.

    Args:
        value: Resolved value; strings are returned literally without evaluation.
        binding_name_to_value: Complete output selection; omission or UNSET selects initialized declared outputs.
            {} selects no writes.
    """
    return StepResponse(binding_name_to_value=binding_name_to_value, outcome=Return(value))


def break_response(*, binding_name_to_value: Mapping[str, object] | UnsetType = UNSET) -> StepResponse:
    """Create a response with break outcome (exit enclosing loop)."""
    return StepResponse(outcome=Break(), binding_name_to_value=binding_name_to_value)


def continue_response(*, binding_name_to_value: Mapping[str, object] | UnsetType = UNSET) -> StepResponse:
    """Create a response with continue outcome (skip to next iteration)."""
    return StepResponse(outcome=Continue(), binding_name_to_value=binding_name_to_value)
