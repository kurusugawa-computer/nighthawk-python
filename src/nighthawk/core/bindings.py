"""Shared binding contracts for Natural blocks and Python integrations."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from keyword import iskeyword
from typing import cast

from pydantic import BaseModel, TypeAdapter
from typing_extensions import TypeForm

from nighthawk.errors import NighthawkError


class _MissingAnnotation:
    """Distinguish an absent annotation from explicit object, Any, or None."""


MISSING_ANNOTATION = _MissingAnnotation()


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
    Pydantic accepts model and dataclass instances without revalidating their fields, so each such instance in a value
    whose contract contains them is also checked by ``validate_instance_fields``.
    The check does not rerun the declared contract's validators, and the original objects are retained when it succeeds.
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
            adapter = TypeAdapter(cast(TypeForm[object], expected_type))
            validated_value = adapter.validate_python(validated_name_to_value[name])
            # In-place changes, which the model is told to make through nh_evaluate, would otherwise bypass the contract.
            validate_instance_fields(validated_value, adapter)
            validated_name_to_value[name] = validated_value
        except Exception as exception:
            raise BindingValidationError(name, exception) from exception
    return validated_name_to_value


def validate_instance_fields(value: object, adapter: TypeAdapter[object]) -> None:
    """Check each model and dataclass instance in a value validated by *adapter* against its own class.

    Each instance must validate through a round-trip copy and already hold what that validation produces in place of
    models, dataclasses, lists, and tuples.
    ``model_copy(update=...)`` and ``model_construct`` skip validation, so a dict where a model is declared would
    otherwise be committed as a dict.
    """
    if _contains_instance_schema(adapter.core_schema):
        _validate_instance_fields(value, set())


def _contains_instance_schema(schema: object) -> bool:
    if isinstance(schema, Mapping):
        if schema.get("type") in ("model", "dataclass"):
            return True
        return any(_contains_instance_schema(value) for value in schema.values())
    if isinstance(schema, list | tuple):
        return any(_contains_instance_schema(item) for item in schema)
    return False


def _validate_instance_fields(value: object, visited_id_set: set[int]) -> None:
    if id(value) in visited_id_set:
        return
    visited_id_set.add(id(value))
    if isinstance(value, BaseModel):
        model_class = type(value)
        if model_class.model_config.get("revalidate_instances", "never") == "never":
            _require_validated_shape(value, model_class.model_validate(value.model_dump(round_trip=True, warnings="none")))
        return
    if is_dataclass(value) and not isinstance(value, type):
        configuration = getattr(type(value), "__pydantic_config__", {})
        if configuration.get("revalidate_instances", "never") == "never":
            adapter = TypeAdapter(type(value))
            _require_validated_shape(value, adapter.validate_python(adapter.dump_python(value, round_trip=True, warnings="none")))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_instance_fields(item, visited_id_set)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _validate_instance_fields(item, visited_id_set)


def _require_validated_shape(original: object, validated: object) -> None:
    # Validation may narrow a subclass instance or a NamedTuple to its declared type, so subclasses keep their shape.
    if isinstance(validated, BaseModel) or (is_dataclass(validated) and not isinstance(validated, type)):
        if not isinstance(original, type(validated)):
            raise ValueError(
                f"Found {type(original).__name__} where {type(validated).__name__} is declared; "
                "build the value with its class or model_validate, because model_copy(update=...) and model_construct do not validate"
            )
        field_names = type(validated).model_fields if isinstance(validated, BaseModel) else [field.name for field in fields(validated)]
        for name in field_names:
            _require_validated_shape(getattr(original, name, None), getattr(validated, name, None))
        return
    if isinstance(validated, list | tuple):
        if not isinstance(original, type(validated)) or len(original) != len(validated):
            raise ValueError(f"Found {type(original).__name__} where {type(validated).__name__} is declared")
        for original_item, validated_item in zip(original, validated, strict=True):
            _require_validated_shape(original_item, validated_item)
        return
    if isinstance(validated, Mapping) and isinstance(original, Mapping) and len(original) == len(validated):
        for original_item, validated_item in zip(original.values(), validated.values(), strict=True):
            _require_validated_shape(original_item, validated_item)
