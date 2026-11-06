"""Generate once, evaluate explicitly, and reuse the same source in fresh batch runs.

The text task is deliberately explicit so ordinary Python provides a baseline.
This example verifies composition and source reuse, not a benefit from using a
model for normalization. Evaluation never approves the generated source.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nighthawk.agent import CodeProposal, Grant, RunHandle
from nighthawk.agent.generated import RetainedFunction, retain_function
from nighthawk.agent.generation import FunctionRequest, propose_function
from nighthawk.agent.runtime import AsyncScaffoldComposition, RunContract, Runner, ScaffoldComposition

NORMALIZE_LABELS = FunctionRequest(
    "Define normalize_labels(labels: tuple[str, ...]) -> tuple[str, ...]. Strip leading and trailing whitespace, "
    "casefold each label, discard empty labels, and remove duplicates while preserving first occurrence order. "
    "Use deterministic Python without external dependencies or shared state. Do not call the function.",
    "normalize_labels",
)


@dataclass(frozen=True)
class NormalizationTrial:
    labels: tuple[str, ...]
    result: tuple[str, ...]
    expected: tuple[str, ...]
    matches_criterion: bool
    run_id: str


def evaluate_normalization(labels: tuple[str, ...], result: tuple[str, ...], *, run_id: str) -> NormalizationTrial:
    """Compare this observed result with application-owned criteria, without approval."""
    expected = tuple(dict.fromkeys(label.strip().casefold() for label in labels if label.strip()))
    return NormalizationTrial(labels, result, expected, result == expected, run_id)


async def generate_normalizer(
    *,
    runner: Runner,
    generate_function: Callable[[FunctionRequest, object], Awaitable[CodeProposal]] = propose_function,
    handle: RunHandle | None = None,
) -> RetainedFunction:
    """Use the same generation part as standard conversation, without a Session."""

    async def body(request: FunctionRequest) -> RetainedFunction:
        proposal = await generate_function(request, {"example_input": (" Alpha ", "alpha", "BETA", ""), "example_result": ("alpha", "beta")})
        return retain_function(proposal, export_name=request.export_name)

    return await runner.run(AsyncScaffoldComposition(RunContract(FunctionRequest, RetainedFunction), body), NORMALIZE_LABELS, handle=handle)


def run_normalization_trial(
    function: RetainedFunction,
    labels: tuple[str, ...],
    *,
    runner: Runner,
    run_entry_grant: Grant = Grant.EMPTY,
    handle: RunHandle | None = None,
) -> NormalizationTrial:
    """Start a fresh run, then evaluate the observed output outside its execution."""
    selected_handle = handle if handle is not None else RunHandle()

    def body(value: tuple[str, ...]) -> tuple[str, ...]:
        result = function(value)
        if not isinstance(result, tuple) or any(not isinstance(label, str) for label in result):
            raise TypeError("Normalization must return a tuple of strings")
        return result

    result = runner.run(
        ScaffoldComposition(RunContract(tuple[str, ...], tuple[str, ...]), body, run_entry_grant=run_entry_grant),
        labels,
        handle=selected_handle,
    )
    assert selected_handle.run_id is not None
    return evaluate_normalization(labels, result, run_id=selected_handle.run_id)
