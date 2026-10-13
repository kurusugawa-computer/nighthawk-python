from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import TypeAdapter

from nighthawk.errors import ExecutionError
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.step_contract import StepOutcome


@dataclass(frozen=True)
class StubExecutor:
    def _parse_response(self, request: StepRequest) -> tuple[StepOutcome, dict[str, object]]:
        processed_natural_program = request.processed_natural_program
        step_context = request.step_context
        binding_names = list(request.output_binding_names)
        allowed_step_kinds = request.allowed_step_kinds
        _ = step_context
        _ = allowed_step_kinds

        json_start = processed_natural_program.find("{")
        if json_start == -1:
            raise ExecutionError("Execution expected JSON object in stub mode")

        try:
            data = json.loads(processed_natural_program[json_start:])
        except json.JSONDecodeError as e:
            raise ExecutionError(f"Execution expected JSON (stub mode): {e}") from e

        if not isinstance(data, dict):
            raise ExecutionError("Execution expected JSON object (stub mode)")

        if "step_outcome" not in data:
            raise ExecutionError("Stub execution expected 'step_outcome' in envelope")
        if "bindings" not in data:
            raise ExecutionError("Stub execution expected 'bindings' in envelope")

        try:
            step_outcome = TypeAdapter(StepOutcome).validate_python(data["step_outcome"])
        except Exception as e:
            raise ExecutionError(f"Stub execution has invalid step_outcome: {e}") from e

        bindings_object = data["bindings"]
        if not isinstance(bindings_object, dict):
            raise ExecutionError("Stub execution expected 'bindings' to be an object")

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in bindings_object:
                bindings[name] = bindings_object[name]

        return step_outcome, bindings

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        outcome, bindings = self._parse_response(request)
        return run_coroutine_synchronously(lambda: resolve_model_candidate(request, outcome, bindings))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        outcome, bindings = self._parse_response(request)
        return await resolve_model_candidate(request, outcome, bindings)


@dataclass
class InspectingExecutor:
    handler: Callable[[StepRequest], StepCandidate]

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)


@dataclass
class ModelResponseExecutor:
    outcome: StepOutcome
    binding_name_to_value: dict[str, object] = field(default_factory=dict)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return await resolve_model_candidate(request, self.outcome, self.binding_name_to_value)
