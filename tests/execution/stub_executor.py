from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import TypeAdapter

from nighthawk.errors import ExecutionError
from nighthawk.runtime.step_contract import StepOutcome


@dataclass(frozen=True)
class StubExecutor:
    """Test-only executor that parses a step envelope from the Natural program.

    This is intentionally not part of the library's public API.

    Contract:

    - Find the first '{' in the Natural program text.
    - Parse the substring starting there as a JSON object.
    - Expect an envelope with keys: 'step_outcome' and 'bindings'.
    - Validate 'step_outcome' against `StepOutcome`.
    - Return bindings filtered to only names in the provided binding list.
    """

    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: object,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
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
