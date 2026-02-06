from __future__ import annotations

import json
from dataclasses import dataclass

from nighthawk.errors import ExecutionError
from nighthawk.execution.contracts import EXECUTION_EFFECT_TYPES, ExecutionFinal


@dataclass(frozen=True)
class StubExecutor:
    """Test-only executor that parses an execution envelope from the Natural program.

    This is intentionally not part of the library's public API.

    Contract:

    - Find the first '{' in the Natural program text.
    - Parse the substring starting there as a JSON object.
    - Expect an envelope with keys: 'execution_final' and 'bindings'.
    - Validate 'execution_final' against `ExecutionFinal`.
    - Return bindings filtered to only names in the provided binding list.
    """

    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: object,
        binding_names: list[str],
        is_in_loop: bool,
        allowed_effect_types: tuple[str, ...] = EXECUTION_EFFECT_TYPES,
    ) -> tuple[ExecutionFinal, dict[str, object]]:
        _ = execution_context
        _ = is_in_loop
        _ = allowed_effect_types

        json_start = processed_natural_program.find("{")
        if json_start == -1:
            raise ExecutionError("Execution expected JSON object in stub mode")

        try:
            data = json.loads(processed_natural_program[json_start:])
        except json.JSONDecodeError as e:
            raise ExecutionError(f"Execution expected JSON (stub mode): {e}") from e

        if not isinstance(data, dict):
            raise ExecutionError("Execution expected JSON object (stub mode)")

        if "execution_final" not in data:
            raise ExecutionError("Stub execution expected 'execution_final' in envelope")
        if "bindings" not in data:
            raise ExecutionError("Stub execution expected 'bindings' in envelope")

        try:
            execution_final = ExecutionFinal.model_validate(data["execution_final"])
        except Exception as e:
            raise ExecutionError(f"Stub execution has invalid execution_final: {e}") from e

        bindings_object = data["bindings"]
        if not isinstance(bindings_object, dict):
            raise ExecutionError("Stub execution expected 'bindings' to be an object")

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in bindings_object:
                bindings[name] = bindings_object[name]

        return execution_final, bindings
