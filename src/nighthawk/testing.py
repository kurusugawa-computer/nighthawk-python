"""Test utilities for Nighthawk applications.

Provides test executors and convenience factories for writing
deterministic tests of Natural functions without LLM API calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .runtime.async_bridge import run_coroutine_synchronously
from .runtime.execution_reference import ExecutionReference
from .runtime.model_candidate import resolve_model_candidate
from .runtime.step_context import StepContext
from .runtime.step_contract import (
    BreakStepOutcome,
    ContinueStepOutcome,
    PassStepOutcome,
    RaiseStepOutcome,
    ReturnStepOutcome,
    StepKind,
    StepOutcome,
)
from .runtime.step_request import StepCandidate, StepRequest


@dataclass
class StepCall:
    """Recorded information about a single Natural block execution.

    Attributes:
        execution_reference: Invocation identity shared with oversight and terminal delivery.
        natural_program: The processed Natural block text (after frontmatter removal and interpolation).
        binding_names: Write binding names (``<:name>`` targets) requested by the Natural function.
        binding_name_to_type: Mapping from binding name to its expected type.
            Explicitly annotated bindings carry the declared type; unannotated bindings are inferred from the initial value at runtime.
        allowed_step_kinds: Outcome kinds allowed for this step, determined by syntactic context and deny frontmatter.
        step_locals: Snapshot of step-local variables at the time of execution.
            Contains function parameters and local variables.
        step_globals: Snapshot of referenced module-level names. Filtered to only names that appear as read bindings (``<name>``) and resolve from globals rather than locals.
    """

    execution_reference: ExecutionReference
    natural_program: str
    binding_names: list[str]
    binding_name_to_type: dict[str, object]
    allowed_step_kinds: tuple[StepKind, ...]
    step_locals: dict[str, object]
    step_globals: dict[str, object]
    task_name_to_value: dict[str, object]
    parent_name_to_value: dict[str, object]


@dataclass
class StepResponse:
    """Scripted response for a single Natural block execution.

    Attributes:
        bindings: Proposed output values. Undeclared names fail finalization.
        outcome: The step outcome. Defaults to ``PassStepOutcome``.
    """

    bindings: dict[str, object] = field(default_factory=dict)
    outcome: StepOutcome = field(default_factory=lambda: PassStepOutcome(kind="pass"))


def _build_step_call(
    processed_natural_program: str,
    step_context: StepContext,
    binding_names: list[str],
    allowed_step_kinds: tuple[StepKind, ...],
) -> StepCall:
    referenced_global_names = (
        step_context.read_binding_names | (set(step_context.environment.task_name_to_value) | step_context.type_alias_reference_names)
    ) - step_context.step_locals.keys()
    filtered_globals = {name: step_context.step_globals[name] for name in referenced_global_names if name in step_context.step_globals}
    return StepCall(
        execution_reference=step_context.execution_reference,
        natural_program=processed_natural_program,
        binding_names=list(binding_names),
        binding_name_to_type=dict(step_context.binding_name_to_type),
        allowed_step_kinds=allowed_step_kinds,
        step_locals=dict(step_context.step_locals),
        step_globals=filtered_globals,
        task_name_to_value=dict(step_context.environment.task_name_to_value),
        parent_name_to_value=dict(step_context.environment.parent_name_to_value),
    )


def _apply_response(response: StepResponse, request: StepRequest) -> StepCandidate:
    return run_coroutine_synchronously(lambda: resolve_model_candidate(request, response.outcome, response.bindings))


class ScriptedExecutor:
    """Test executor that returns scripted responses and records calls.

    Responses are consumed in order. Once exhausted, ``default_response`` is
    used for subsequent calls.

    Example::

        from nighthawk.testing import ScriptedExecutor, pass_response

        executor = ScriptedExecutor(responses=[
            pass_response(result="hello world"),
        ])
        with nh.run(executor):
            output = summarize("some text")

        assert output == "hello world"
        assert "result" in executor.calls[0].binding_names
    """

    def __init__(
        self,
        responses: list[StepResponse] | None = None,
        *,
        default_response: StepResponse | None = None,
    ) -> None:
        self.responses: list[StepResponse] = list(responses) if responses else []
        self.default_response: StepResponse = default_response or StepResponse()
        self.calls: list[StepCall] = []

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        call = _build_step_call(
            request.processed_natural_program, request.step_context, list(request.output_binding_names), request.allowed_step_kinds
        )
        self.calls.append(call)
        index = len(self.calls) - 1
        response = self.responses[index] if index < len(self.responses) else self.default_response
        return _apply_response(response, request)

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        call = _build_step_call(
            request.processed_natural_program, request.step_context, list(request.output_binding_names), request.allowed_step_kinds
        )
        self.calls.append(call)
        index = len(self.calls) - 1
        response = self.responses[index] if index < len(self.responses) else self.default_response
        return await resolve_model_candidate(request, response.outcome, response.bindings)


class CallbackExecutor:
    """Test executor that delegates to a user-provided callback function.

    Use when response logic depends on the Natural block input (e.g.,
    routing different binding values based on the program text).

    Example::

        from nighthawk.testing import CallbackExecutor, StepCall, pass_response

        def handler(call: StepCall) -> StepResponse:
            if "urgent" in call.natural_program:
                return pass_response(priority="high")
            return pass_response(priority="normal")

        executor = CallbackExecutor(handler)
        with nh.run(executor):
            result = classify(ticket)
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
            request.processed_natural_program, request.step_context, list(request.output_binding_names), request.allowed_step_kinds
        )
        self.calls.append(call)
        response = self.handler(call)
        return _apply_response(response, request)

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate:
        call = _build_step_call(
            request.processed_natural_program, request.step_context, list(request.output_binding_names), request.allowed_step_kinds
        )
        self.calls.append(call)
        response = self.handler(call)
        return await resolve_model_candidate(request, response.outcome, response.bindings)


# ── Convenience response factories ──


def pass_response(**bindings: object) -> StepResponse:
    """Create a response with pass outcome and optional binding values."""
    return StepResponse(bindings=bindings)


def raise_response(message: str, *, error_type: str | None = None) -> StepResponse:
    """Create a response with raise outcome."""
    return StepResponse(
        outcome=RaiseStepOutcome(
            kind="raise",
            raise_message=message,
            raise_error_type=error_type,
        ),
    )


def return_response(expression: str, **bindings: object) -> StepResponse:
    """Create a response with return outcome.

    The ``expression`` is a Python expression evaluated against
    step locals and globals (e.g. ``"result"`` or ``"len(items)"``).
    """
    return StepResponse(
        bindings=bindings,
        outcome=ReturnStepOutcome(
            kind="return",
            return_expression=expression,
        ),
    )


def break_response() -> StepResponse:
    """Create a response with break outcome (exit enclosing loop)."""
    return StepResponse(outcome=BreakStepOutcome(kind="break"))


def continue_response() -> StepResponse:
    """Create a response with continue outcome (skip to next iteration)."""
    return StepResponse(outcome=ContinueStepOutcome(kind="continue"))
