from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter

from nighthawk.adapters.pydantic_ai.model_candidate import resolve_model_candidate
from nighthawk.adapters.pydantic_ai.model_outcome import ModelOutcome
from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.configuration import UNSET, UnsetType
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.errors import NighthawkError


async def resolve_proposal(request: StepRequest, outcome: ModelOutcome, binding_name_to_value: Mapping[str, object]) -> StepCandidate:
    """Stand in for model assignments before the final response, then select exactly the proposed outputs."""
    for name, value in binding_name_to_value.items():
        request.step_context.environment.assign(name, value)
    candidate = await resolve_model_candidate(request, outcome)
    return StepCandidate(outcome=candidate.outcome, binding_name_to_value=binding_name_to_value)


@dataclass(frozen=True)
class StubExecutor:
    def _parse_response(self, request: StepRequest) -> tuple[ModelOutcome, dict[str, object]]:
        processed_natural_program = request.processed_natural_program
        step_context = request.step_context
        binding_names = list(request.output_binding_names)
        allowed_outcome_kinds = request.allowed_outcome_kinds
        _ = step_context
        _ = allowed_outcome_kinds

        json_start = processed_natural_program.find("{")
        if json_start == -1:
            raise NighthawkError("Execution expected JSON object in stub mode")

        try:
            data = json.loads(processed_natural_program[json_start:])
        except json.JSONDecodeError as e:
            raise NighthawkError(f"Execution expected JSON (stub mode): {e}") from e

        if not isinstance(data, dict):
            raise NighthawkError("Execution expected JSON object (stub mode)")

        if "step_outcome" not in data:
            raise NighthawkError("Stub execution expected 'step_outcome' in envelope")
        if "bindings" not in data:
            raise NighthawkError("Stub execution expected 'bindings' in envelope")

        try:
            step_outcome = TypeAdapter(ModelOutcome).validate_python(data["step_outcome"])
        except Exception as e:
            raise NighthawkError(f"Stub execution has invalid step_outcome: {e}") from e

        bindings_object = data["bindings"]
        if not isinstance(bindings_object, dict):
            raise NighthawkError("Stub execution expected 'bindings' to be an object")

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in bindings_object:
                bindings[name] = bindings_object[name]

        return step_outcome, bindings

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        outcome, bindings = self._parse_response(request)
        return run_coroutine_synchronously(lambda: resolve_proposal(request, outcome, bindings))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        outcome, bindings = self._parse_response(request)
        return await resolve_proposal(request, outcome, bindings)


@dataclass
class InspectingExecutor:
    handler: Callable[[StepRequest], StepCandidate]

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)


@dataclass
class ModelResponseExecutor:
    outcome: ModelOutcome
    binding_name_to_value: Mapping[str, object] | UnsetType = UNSET

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        if isinstance(self.binding_name_to_value, UnsetType):
            return await resolve_model_candidate(request, self.outcome)
        return await resolve_proposal(request, self.outcome, self.binding_name_to_value)
