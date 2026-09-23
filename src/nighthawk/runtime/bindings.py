"""Shared binding contracts for Natural blocks and Python integrations."""

from collections.abc import Iterable, Mapping, Sequence
from keyword import iskeyword

from pydantic import TypeAdapter

from ..errors import NighthawkError


class MissingAnnotation:
    """Distinguish an absent annotation from explicit object, Any, or None."""


MISSING_ANNOTATION = MissingAnnotation()


class BindingError(NighthawkError):
    """Base error for binding declarations and value contracts."""


class BindingDeclarationError(BindingError):
    """Reject malformed declarations or values outside their declared names."""

    def __init__(self, message: str, *, binding_names: tuple[str, ...] = ()) -> None:
        self.binding_names = binding_names
        super().__init__(message)


def normalize_binding_names(names: Sequence[str]) -> tuple[str, ...]:
    """Snapshot ordered, unique ASCII Python identifiers, excluding keywords."""
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise BindingDeclarationError("Binding declarations must be an ordered sequence of names")
    normalized = tuple(names)
    seen: set[str] = set()
    for name in normalized:
        if not isinstance(name, str):
            raise BindingDeclarationError("Binding names must be strings")
        if not name.isascii() or not name.isidentifier() or iskeyword(name):
            raise BindingDeclarationError(f"Invalid binding name: {name!r}", binding_names=(name,))
        if name in seen:
            raise BindingDeclarationError(f"Duplicate binding name: {name!r}", binding_names=(name,))
        seen.add(name)
    return normalized


def require_declared_binding_names(names: Iterable[str], declared_names: Sequence[str]) -> None:
    """Reject undeclared names before invoking validators or performing effects."""
    normalized = normalize_binding_names(tuple(names))
    undeclared = tuple(sorted(set(normalized) - set(declared_names)))
    if undeclared:
        raise BindingDeclarationError(f"Undeclared output binding names: {list(undeclared)}", binding_names=undeclared)


class BindingValidationError(BindingError):
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
    output_binding_names: Sequence[str],
) -> dict[str, object]:
    """Validate only supplied candidates and return a separate mapping.

    Undeclared names are rejected before any value validation.
    Declarations must be an ordered sequence of unique binding names.
    Validation follows declaration order.
    Missing contracts or ``MISSING_ANNOTATION`` skip validation; explicit ``None`` requires None.
    Contracts must already be resolved Python annotations.
    No assignment, initial-value supplementation, or assignment recording occurs here.
    Values are not deep-copied, and validator effects are not rolled back.
    """
    output_binding_names = normalize_binding_names(output_binding_names)
    require_declared_binding_names(binding_name_to_value, output_binding_names)
    require_declared_binding_names(binding_name_to_type, output_binding_names)
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
