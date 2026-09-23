"""Shared binding contracts for Natural blocks and Python integrations."""

from collections.abc import Iterable, Mapping

from pydantic import TypeAdapter

from ..errors import ExecutionError
from .annotations import MISSING_ANNOTATION


class BindingValidationError(ValueError):
    """Identify a failed binding while preserving the validator's exception as the cause."""

    def __init__(self, binding_name: str, original_exception: Exception) -> None:
        self.binding_name = binding_name
        self.original_exception = original_exception
        super().__init__(f"Output binding {binding_name!r} failed validation: {original_exception}")


def infer_binding_types(binding_name_to_type: Mapping[str, object], initial_name_to_value: Mapping[str, object]) -> dict[str, object]:
    """Return inferred contracts without changing either input mapping.

    Only entries equal by identity to ``MISSING_ANNOTATION`` are inferred.
    Explicit ``object``, ``Any``, and ``None`` remain unchanged.
    Missing initial values, ``None``, and exact ``object`` instances select ``object``.
    Containers use their runtime class, without inspecting their contents.
    Names absent from the contract mapping are not added.
    """
    inferred_name_to_type = dict(binding_name_to_type)
    for name, declared_type in binding_name_to_type.items():
        if declared_type is not MISSING_ANNOTATION:
            continue
        initial_type = type(initial_name_to_value.get(name))
        inferred_name_to_type[name] = object if initial_type is type(None) else initial_type
    return inferred_name_to_type


def validate_output_bindings(
    binding_name_to_value: Mapping[str, object],
    binding_name_to_type: Mapping[str, object],
    output_binding_names: Iterable[str],
) -> dict[str, object]:
    """Validate only supplied candidates and return a separate mapping.

    Undeclared names are rejected before any value validation.
    Validation follows declaration iteration order; pass a sequence when order matters.
    Missing contracts or ``MISSING_ANNOTATION`` skip validation; explicit ``None`` requires None.
    Contracts must already be resolved Python annotations.
    No assignment, initial-value supplementation, or assignment recording occurs here.
    Values are not deep-copied, and validator effects are not rolled back.
    """
    output_binding_names = tuple(dict.fromkeys(output_binding_names))
    undeclared_names = binding_name_to_value.keys() - set(output_binding_names)
    if undeclared_names:
        raise ExecutionError(f"Undeclared output binding names: {sorted(undeclared_names)}")
    validated_name_to_value = dict(binding_name_to_value)
    for name in output_binding_names:
        expected_type = binding_name_to_type.get(name, MISSING_ANNOTATION)
        if name not in validated_name_to_value or expected_type is MISSING_ANNOTATION:
            continue
        try:
            validated_name_to_value[name] = TypeAdapter(expected_type).validate_python(validated_name_to_value[name])
        except Exception as exception:
            raise BindingValidationError(name, exception) from exception
    return validated_name_to_value
