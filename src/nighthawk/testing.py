"""Test utilities for Nighthawk applications.

Provides test executors and convenience factories for writing
deterministic tests of Natural functions without LLM API calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .runtime.step_context import StepContext
from .runtime.step_contract import (
    BreakStepOutcome,
    ContinueStepOutcome,
    PassStepOutcome,
    RaiseStepOutcome,
    ReturnStepOutcome,
    StepOutcome,
)


@dataclass
class StepCall:
    """Recorded information about a single Natural block execution.

    Attributes:
        natural_program: The processed Natural block text (after frontmatter
            removal and interpolation).
        binding_names: Write binding names (``<:name>`` targets) requested by
            the Natural function.
        binding_name_to_type: Mapping from binding name to its expected type.
            Explicitly annotated bindings carry the declared type; unannotated
            bindings are inferred from the initial value at runtime.
        allowed_step_kinds: Outcome kinds allowed for this step, determined by
            syntactic context and deny frontmatter.
        step_locals: Snapshot of step-local variables at the time of execution.
            Contains function parameters and local variables.
        step_globals: Snapshot of referenced module-level names. Filtered to
            only names that appear as read bindings (``<name>``) and resolve
            from globals rather than locals.
    """

    natural_program: str
    binding_names: list[str]
    binding_name_to_type: dict[str, object]
    allowed_step_kinds: tuple[str, ...]
    step_locals: dict[str, object]
    step_globals: dict[str, object]


@dataclass
class StepResponse:
    """Scripted response for a single Natural block execution.

    Attributes:
        bindings: Mapping from write binding names to their values. Names not
            in the step's ``binding_names`` are silently ignored.
        outcome: The step outcome. Defaults to ``PassStepOutcome``.
    """

    bindings: dict[str, object] = field(default_factory=dict)
    outcome: StepOutcome = field(default_factory=lambda: PassStepOutcome(kind="pass"))


def _build_step_call(
    processed_natural_program: str,
    step_context: StepContext,
    binding_names: list[str],
    allowed_step_kinds: tuple[str, ...],
) -> StepCall:
    referenced_global_names = step_context.read_binding_names - step_context.step_locals.keys()
    filtered_globals = {name: step_context.step_globals[name] for name in referenced_global_names if name in step_context.step_globals}
    return StepCall(
        natural_program=processed_natural_program,
        binding_names=list(binding_names),
        binding_name_to_type=dict(step_context.binding_name_to_type),
        allowed_step_kinds=allowed_step_kinds,
        step_locals=dict(step_context.step_locals),
        step_globals=filtered_globals,
    )


def _apply_response(
    response: StepResponse,
    binding_names: list[str],
) -> tuple[StepOutcome, dict[str, object]]:
    binding_name_set = set(binding_names)
    filtered_bindings = {name: value for name, value in response.bindings.items() if name in binding_name_set}
    return response.outcome, filtered_bindings


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
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        call = _build_step_call(processed_natural_program, step_context, binding_names, allowed_step_kinds)
        self.calls.append(call)
        index = len(self.calls) - 1
        response = self.responses[index] if index < len(self.responses) else self.default_response
        return _apply_response(response, binding_names)


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
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        call = _build_step_call(processed_natural_program, step_context, binding_names, allowed_step_kinds)
        self.calls.append(call)
        response = self.handler(call)
        return _apply_response(response, binding_names)


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


def return_response(reference_path: str, **bindings: object) -> StepResponse:
    """Create a response with return outcome.

    The ``reference_path`` must name a binding that the runner can resolve
    from step locals (e.g. ``"result"`` or ``"result.field"``).
    """
    return StepResponse(
        bindings=bindings,
        outcome=ReturnStepOutcome(
            kind="return",
            return_reference_path=reference_path,
        ),
    )


def break_response() -> StepResponse:
    """Create a response with break outcome (exit enclosing loop)."""
    return StepResponse(outcome=BreakStepOutcome(kind="break"))


def continue_response() -> StepResponse:
    """Create a response with continue outcome (skip to next iteration)."""
    return StepResponse(outcome=ContinueStepOutcome(kind="continue"))
