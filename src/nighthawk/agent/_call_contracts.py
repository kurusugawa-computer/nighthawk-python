"""Shared value and Python call contracts for host-owned mediation.

Validation establishes value contracts; it does not establish judgment quality or authority.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from pydantic import ConfigDict, PydanticSchemaGenerationError, TypeAdapter
from typing_extensions import TypeForm


@dataclass(frozen=True)
class CallContract:
    signature: inspect.Signature | None
    parameter_name_to_adapter: Mapping[str, TypeAdapter[object]]
    return_adapter: TypeAdapter[object] | None


def _resolve_hints(function: Callable[..., object]) -> dict[str, object]:
    annotation_owner = function
    while isinstance(annotation_owner, partial):
        annotation_owner = annotation_owner.func
    if not inspect.isroutine(annotation_owner) and not isinstance(annotation_owner, type):
        annotation_owner = annotation_owner.__call__  # type: ignore[operator]
    try:
        return dict(typing.get_type_hints(annotation_owner, include_extras=True))
    except Exception as error:
        # A deferred adapter would resolve names in its own module or caller,
        # potentially accepting a different type with the same name.
        raise ValueError("declared annotations could not be resolved in their defining namespace") from error


def build_value_adapter(hint: object) -> TypeAdapter[object]:
    """Validate declared values, preserving ordinary application object identity."""

    type_form = typing.cast(TypeForm[object], hint)
    try:
        return TypeAdapter(type_form)
    except PydanticSchemaGenerationError:
        # Ordinary application objects retain identity and use an instance check.
        # Models with their own configuration already succeeded above.
        try:
            return TypeAdapter(type_form, config=ConfigDict(arbitrary_types_allowed=True))
        except Exception as error:
            raise ValueError("declared type cannot be validated") from error


def build_call_contract(function: Callable[..., object]) -> CallContract:
    """Build validators without silently discarding an explicit annotation.

    Ordinary application classes use instance checks and preserve object identity.
    Unannotated values, object, and Any remain unconstrained.
    Unresolved annotations and unsupported types fail construction.
    Instance checks do not validate an arbitrary object's internals.
    """

    signature: inspect.Signature | None
    try:
        signature = inspect.signature(function, eval_str=True)
    except (NameError, TypeError, ValueError):
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):
            signature = None
    annotation_name_to_type = _resolve_hints(function)
    parameter_name_to_adapter: dict[str, TypeAdapter[object]] = {}
    if signature is not None:
        for parameter in signature.parameters.values():
            if parameter.name not in annotation_name_to_type:
                continue
            hint = annotation_name_to_type[parameter.name]
            if hint is not object and hint is not typing.Any:
                parameter_name_to_adapter[parameter.name] = build_value_adapter(hint)
    return_adapter = build_value_adapter(annotation_name_to_type["return"]) if "return" in annotation_name_to_type else None
    return CallContract(signature=signature, parameter_name_to_adapter=parameter_name_to_adapter, return_adapter=return_adapter)


def bind_and_validate_arguments(
    contract: CallContract,
    positional_arguments: tuple[object, ...],
    keyword_argument_name_to_value: Mapping[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    """Bind arguments, validate supplied values, and expose the effective defaults.

    Variadic annotations describe each positional item or keyword value, as in
    ordinary Python typing. Author-owned defaults retain their original identity
    and state, including mutable defaults; they are included for input evidence
    without copying or coercing them on each invocation.

    Raises:
        ValueError: when binding or validation fails; the message names the
            parameter but never echoes argument values.
    """

    if contract.signature is None:
        return positional_arguments, dict(keyword_argument_name_to_value)
    try:
        bound = contract.signature.bind(*positional_arguments, **keyword_argument_name_to_value)
    except TypeError as error:
        raise ValueError(f"arguments do not match the declared signature: {error}") from error
    for parameter_name, adapter in contract.parameter_name_to_adapter.items():
        if parameter_name not in bound.arguments:
            continue
        try:
            value = bound.arguments[parameter_name]
            kind = contract.signature.parameters[parameter_name].kind
            if kind is inspect.Parameter.VAR_POSITIONAL:
                bound.arguments[parameter_name] = tuple(adapter.validate_python(item) for item in value)
            elif kind is inspect.Parameter.VAR_KEYWORD:
                bound.arguments[parameter_name] = {name: adapter.validate_python(item) for name, item in value.items()}
            else:
                bound.arguments[parameter_name] = adapter.validate_python(value)
        except Exception as error:
            raise ValueError(f"argument {parameter_name!r} does not satisfy its declared type") from error
    bound.apply_defaults()
    return tuple(bound.args), dict(bound.kwargs)


def validate_result(contract: CallContract, result: object) -> object:
    """Validate a result against the declared return type and return the validated value.

    The returned value is the one that satisfies the declared type; when the
    declared type coerces (for example ``"123"`` into ``int``), the coerced
    value is what the host commits, never the raw value.
    """

    if contract.return_adapter is None:
        return result
    try:
        return contract.return_adapter.validate_python(result)
    except Exception as error:
        raise ValueError("result does not satisfy the declared return type") from error
