"""Static, best-effort metadata shared by context rendering and value inspection.

Application descriptors, annotation expressions, serializers, and repr methods
are not executed to obtain a description. Unknown values remain opaque.
"""

from __future__ import annotations

import ast
import functools
import inspect
import sys
import tokenize
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAliasType

from .annotations import SourceAnnotation

_ABSENT = object()


def exact_type[T](value: object, expected: type[T]) -> typing.TypeIs[T]:
    return type(value) is expected


def _is_class(value: object) -> typing.TypeIs[type]:
    return any(base is type for base in class_bases(type(value)))


def type_name(value: object) -> str:
    return class_name(type(value))


def class_name(value: type) -> str:
    return type.__dict__["__name__"].__get__(value)


def class_namespace(value: type) -> Mapping[str, object]:
    return type.__dict__["__dict__"].__get__(value)


def class_bases(value: type) -> tuple[type, ...]:
    return type.__dict__["__mro__"].__get__(value)


def _class_attribute(value: type, name: str) -> object:
    for base in class_bases(value):
        namespace = class_namespace(base)
        if name in namespace:
            return namespace[name]
    return _ABSENT


def _is_data_descriptor(value: object) -> bool:
    return _class_attribute(type(value), "__get__") is not _ABSENT and (
        _class_attribute(type(value), "__set__") is not _ABSENT or _class_attribute(type(value), "__delete__") is not _ABSENT
    )


def static_attribute(value: object, name: str, default: object = _ABSENT) -> object:
    """Read static dictionaries, bypassing even metaclass attribute hooks."""
    class_attribute = _class_attribute(type(value), name)
    if _is_data_descriptor(class_attribute):
        return class_attribute
    own_attribute = _class_attribute(value, name) if _is_class(value) else _instance_namespace(value).get(name, _ABSENT)
    if own_attribute is not _ABSENT:
        return own_attribute
    return default if class_attribute is _ABSENT else class_attribute


def annotation_arguments(annotation: object) -> tuple[object, ...]:
    annotation_type = type(annotation)
    known_annotation_types = (
        types.GenericAlias,
        types.UnionType,
        type(typing.List[int]),  # noqa: UP006 - the legacy typing object has a distinct runtime class.
        type(typing.Union[int, str]),  # noqa: UP007 - support the legacy typing runtime class too.
        type(typing.Literal[1]),
        type(typing.Annotated[int, "metadata"]),
        type(typing.Callable[[int], str]),
        type(Callable[[int], str]),
    )
    if any(annotation_type is candidate for candidate in known_annotation_types):
        return typing.get_args(annotation)
    return ()


def annotation_text(annotation: object, *, depth: int = 0) -> str:
    if depth > 6:
        return "<annotation omitted>"
    if annotation is None or annotation is type(None):
        return "None"
    if type(annotation) is str:
        return annotation if len(annotation) <= 500 else annotation[:500] + "<snipped annotation spelling>"
    if type(annotation) is SourceAnnotation:
        spelling = " | ".join(annotation.expressions)[:500]
        if annotation.parameter_kind is not None:
            return f"{spelling} ({annotation.parameter_kind} variadic parameter)"
        return spelling
    if type(annotation) is typing.TypeAliasType:
        return annotation.__name__
    if _is_class(annotation):
        return class_name(annotation)
    if annotation is typing.Any:
        return "Any"
    arguments = annotation_arguments(annotation)
    if arguments:
        origin = typing.get_origin(annotation)
        if origin is types.UnionType or origin is typing.Union:
            return " | ".join(annotation_text(item, depth=depth + 1) for item in arguments)
        if origin is typing.Literal:
            return f"Literal[{', '.join(safe_literal(item) for item in arguments)}]"
        if origin is typing.Annotated:
            return f"Annotated[{annotation_text(arguments[0], depth=depth + 1)}, {', '.join(safe_literal(item) for item in arguments[1:])}]"
        if origin is Callable and len(arguments) == 2:
            parameters, result = arguments
            if exact_type(parameters, list):
                parameter_text = f"[{', '.join(annotation_text(item, depth=depth + 1) for item in parameters)}]"
            else:
                parameter_text = "..." if parameters is Ellipsis else annotation_text(parameters, depth=depth + 1)
            return f"Callable[{parameter_text}, {annotation_text(result, depth=depth + 1)}]"
        origin_text = class_name(origin) if _is_class(origin) else "Callable" if origin is typing.Callable else type_name(annotation)
        return f"{origin_text}[{', '.join(annotation_text(item, depth=depth + 1) for item in arguments)}]"
    return f"<opaque {type_name(annotation)} annotation>"


def safe_preview(value: object, *, depth: int = 2, max_items: int = 8, max_string_length: int = 160) -> object:
    """Return JSON-compatible preview data without invoking application code."""
    value_type = type(value)
    if exact_type(value, int) and value.bit_length() > 512:
        return "<large int; preview omitted>"
    if value is None or any(value_type is candidate for candidate in (bool, int, float)):
        return value
    if exact_type(value, str):
        return value if len(value) <= max_string_length else value[:max_string_length] + "<snipped>"
    if exact_type(value, bytes) or exact_type(value, bytearray):
        return f"<{class_name(value_type)}; {len(value)} bytes>"
    if exact_type(value, list) or exact_type(value, tuple):
        if depth <= 0:
            return f"<{class_name(value_type)}; {len(value)} items>"
        result = [safe_preview(item, depth=depth - 1, max_items=max_items, max_string_length=max_string_length) for item in value[:max_items]]
        if len(value) > max_items:
            result.append(f"<snipped {len(value) - max_items} items; use a Python slice>")
        return result
    if exact_type(value, dict):
        if depth <= 0:
            return f"<dict; {len(value)} entries>"
        key_to_preview: dict[str, object] = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= max_items:
                key_to_preview["<snipped>"] = "More entries are available through Python iteration"
                break
            key_text = key if type(key) is str else safe_literal(key)
            key_to_preview[key_text] = safe_preview(item, depth=depth - 1, max_items=max_items, max_string_length=max_string_length)
        return key_to_preview
    if any(base is tuple for base in class_bases(value_type)):
        field_names = static_attribute(value_type, "_fields")
        if exact_type(field_names, tuple) and all(exact_type(name, str) for name in field_names):
            if depth <= 0:
                return f"<{class_name(value_type)}; fields omitted>"
            field_name_to_preview = {
                name: safe_preview(
                    tuple.__getitem__(typing.cast(tuple, value), i), depth=depth - 1, max_items=max_items, max_string_length=max_string_length
                )
                for i, name in enumerate(field_names[:max_items])
            }
            if len(field_names) > max_items:
                field_name_to_preview["<snipped>"] = "More named fields are available through inspection or Python access"
            return field_name_to_preview
    from pydantic_ai.messages import AudioUrl, BinaryContent, DocumentUrl, ImageUrl, UploadedFile, VideoUrl

    if any(value_type is candidate for candidate in (BinaryContent, ImageUrl, AudioUrl, DocumentUrl, VideoUrl, UploadedFile)):
        namespace = _instance_namespace(value)
        return {
            name: safe_preview(item, depth=depth - 1, max_items=max_items, max_string_length=max_string_length)
            for name, item in namespace.items()
            if name in ("kind", "media_type", "url", "file_id", "provider_name", "data")
        }
    return f"<opaque {type_name(value)}>"


def safe_literal(value: object) -> str:
    if exact_type(value, _DisplayText):
        return value.text
    if value is Ellipsis:
        return "..."
    if exact_type(value, int) and value.bit_length() > 512:
        return "<large int>"
    if value is None or any(type(value) is candidate for candidate in (str, bool, int, float, bytes)):
        return repr(value)[:500]
    return f"<opaque {type_name(value)}>"


@dataclass(frozen=True)
class _DisplayText:
    text: str

    def __repr__(self) -> str:
        return self.text


def _signature_text(signature: inspect.Signature) -> str:
    parameters = [
        parameter.replace(
            annotation=inspect.Parameter.empty
            if parameter.annotation is inspect.Parameter.empty
            else _DisplayText(annotation_text(parameter.annotation)),
            default=inspect.Parameter.empty if parameter.default is inspect.Parameter.empty else _DisplayText(safe_literal(parameter.default)),
        )
        for parameter in signature.parameters.values()
    ]
    return_annotation = signature.return_annotation
    text = str(
        signature.replace(
            parameters=parameters,
            return_annotation=inspect.Signature.empty
            if return_annotation is inspect.Signature.empty
            else _DisplayText(annotation_text(return_annotation)),
        )
    )
    return text if len(text) <= 2_000 else text[:2_000] + "<snipped signature metadata>"


def _is_supported_signature(value: object) -> typing.TypeIs[inspect.Signature]:
    return exact_type(value, inspect.Signature) and all(exact_type(parameter, inspect.Parameter) for parameter in value.parameters.values())


@dataclass(frozen=True)
class CallableDescription:
    signature: inspect.Signature | None
    documentation: str | None
    asynchronous: bool
    annotation_note: str | None = None

    @property
    def signature_text(self) -> str | None:
        return None if self.signature is None else _signature_text(self.signature)

    @property
    def annotations(self) -> tuple[object, ...]:
        if self.signature is None:
            return ()
        return tuple(
            annotation
            for annotation in (*[parameter.annotation for parameter in self.signature.parameters.values()], self.signature.return_annotation)
            if annotation is not inspect.Signature.empty
        )


@functools.lru_cache(maxsize=64)
def _source_annotation_inventory(filename: str, modified: int, size: int) -> dict[tuple[str, int], dict[str, object]]:
    # Read a bounded source file directly. inspect.getsource/linecache may call
    # an application loader, and annotationlib may call a custom __annotate__.
    del modified
    if size > 1_048_576:
        return {}
    try:
        with tokenize.open(filename) as stream:
            source = stream.read(1_048_577)
        if len(source) > 1_048_576:
            return {}
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeError):
        return {}
    function_location_to_annotation: dict[tuple[str, int], dict[str, object]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        annotation_name_to_value: dict[str, object] = {
            argument.arg: ast.unparse(argument.annotation)
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *([node.args.vararg] if node.args.vararg else []),
                *([node.args.kwarg] if node.args.kwarg else []),
            )
            if argument.annotation is not None
        }
        if node.returns is not None:
            annotation_name_to_value["return"] = ast.unparse(node.returns)
        function_location_to_annotation[node.name, first_line] = annotation_name_to_value
    return function_location_to_annotation


def _function_annotations(value: types.FunctionType) -> tuple[dict[str, object], str | None]:
    if sys.version_info < (3, 14) or value.__annotate__ is None:
        return dict(value.__annotations__), None
    filename = value.__code__.co_filename
    try:
        metadata = Path(filename).stat()
    except OSError:
        return {}, "Deferred annotations omitted: source unavailable; annotation evaluator was not called."
    annotations = _source_annotation_inventory(filename, metadata.st_mtime_ns, metadata.st_size).get(
        (value.__code__.co_name, value.__code__.co_firstlineno)
    )
    if annotations is None:
        return {}, "Deferred annotations omitted: source unavailable or unsupported; annotation evaluator was not called."
    return dict(
        annotations
    ), "Annotations retain source spelling and were not evaluated; deferred or modified runtime annotation metadata was not read."


def describe_callable(value: object, *, depth: int = 0) -> CallableDescription:
    if depth > 8:
        return CallableDescription(None, None, False)
    value_type = type(value)
    signature: inspect.Signature | None = None
    documentation: object = None
    asynchronous = False
    annotation_note: str | None = None
    if exact_type(value, types.FunctionType):
        documentation = value.__doc__
        asynchronous = bool(value.__code__.co_flags & inspect.CO_COROUTINE)
        declared_signature = value.__dict__.get("__signature__", _ABSENT)
        wrapped = value.__dict__.get("__wrapped__", _ABSENT)
        if declared_signature is _ABSENT and wrapped is not _ABSENT:
            description = describe_callable(wrapped, depth=depth + 1)
            return CallableDescription(description.signature, description.documentation, asynchronous, description.annotation_note)
        if _is_supported_signature(declared_signature):
            signature = declared_signature
        elif declared_signature is _ABSENT or declared_signature is None:
            # A fresh function carries no __wrapped__, custom __signature__, or
            # deferred annotation evaluator. inspect.signature only reads it.
            mirror = types.FunctionType(value.__code__, {}, value.__name__, value.__defaults__, value.__closure__)
            mirror.__kwdefaults__ = value.__kwdefaults__
            mirror.__annotations__, annotation_note = _function_annotations(value)
            signature = inspect.signature(mirror, follow_wrapped=False, eval_str=False)
    elif exact_type(value, types.MethodType):
        description = describe_callable(value.__func__, depth=depth + 1)
        signature = description.signature
        if signature is not None:
            parameters = list(signature.parameters.values())
            if parameters and parameters[0].kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                signature = signature.replace(parameters=parameters[1:])
        return CallableDescription(signature, description.documentation, description.asynchronous, description.annotation_note)
    elif exact_type(value, functools.partial):
        description = describe_callable(value.func, depth=depth + 1)
        if description.signature is not None:

            def mirror(*items: object, **name_to_value: object) -> None:
                pass

            mirror.__signature__ = description.signature  # type: ignore[attr-defined]
            arguments = tuple(_DisplayText(safe_literal(item)) for item in value.args)
            keyword_name_to_value = {name: _DisplayText(safe_literal(item)) for name, item in (value.keywords or {}).items()}
            with suppress(TypeError, ValueError):
                signature = inspect.signature(functools.partial(mirror, *arguments, **keyword_name_to_value), eval_str=False)
        return CallableDescription(signature, description.documentation, description.asynchronous, description.annotation_note)
    elif exact_type(value, types.BuiltinFunctionType):
        documentation = value.__doc__
        if value.__module__ == "builtins" or any(type(value.__self__) is candidate for candidate in (list, dict, str, set, tuple, bytes)):
            with suppress(TypeError, ValueError):
                signature = inspect.signature(value, eval_str=False)
    else:
        declared_signature = static_attribute(value, "__signature__")
        if _is_supported_signature(declared_signature):
            signature = declared_signature
        elif declared_signature is _ABSENT:
            call = static_attribute(value_type, "__call__")
            if type(call) is types.FunctionType:
                return describe_callable(types.MethodType(call, value), depth=depth + 1)
        documentation = static_attribute(value, "__doc__", None)
    return CallableDescription(
        signature, inspect.cleandoc(documentation)[:2_000] if type(documentation) is str else None, asynchronous, annotation_note
    )


def _instance_namespace(value: object) -> dict[str, object]:
    descriptor = _class_attribute(type(value), "__dict__")
    if type(descriptor) is types.GetSetDescriptorType and descriptor.__name__ == "__dict__":
        try:
            namespace = descriptor.__get__(value, type(value))
        except (AttributeError, TypeError):
            return {}
        if type(namespace) is dict:
            return namespace
    return {}


@dataclass(frozen=True)
class PublicMembers:
    method_name_to_value: Mapping[str, object]
    field_name_to_value: Mapping[str, object]
    omitted_names: tuple[str, ...]


def describe_public_members(value: object) -> PublicMembers:
    from pydantic import BaseModel

    name_to_attribute: dict[str, object] = {}
    model_fields = static_attribute(type(value), "__pydantic_fields__", {})
    is_model = any(base is BaseModel for base in class_bases(type(value)))
    for base in class_bases(type(value)):
        if base is BaseModel or base is object:
            break
        for name, attribute in class_namespace(base).items():
            if type(name) is str and not name.startswith("_"):
                name_to_attribute.setdefault(name, attribute)
    instance_name_to_value = _instance_namespace(value)
    for name, item in instance_name_to_value.items():
        if type(name) is str and not name.startswith("_"):
            name_to_attribute[name] = static_attribute(value, name, item)
    method_name_to_value: dict[str, object] = {}
    field_name_to_value: dict[str, object] = {}
    omitted_names: list[str] = []
    for name, attribute in sorted(name_to_attribute.items()):
        attribute_type = type(attribute)
        if exact_type(attribute, types.FunctionType):
            method_name_to_value[name] = types.MethodType(attribute, value) if name not in instance_name_to_value else attribute
        elif exact_type(attribute, staticmethod):
            method_name_to_value[name] = attribute.__func__
        elif exact_type(attribute, classmethod) and exact_type(attribute.__func__, types.FunctionType):
            method_name_to_value[name] = types.MethodType(attribute.__func__, type(value))
        elif exact_type(attribute, types.MemberDescriptorType):
            try:
                field_name_to_value[name] = attribute.__get__(value, type(value))
            except (AttributeError, TypeError):
                omitted_names.append(name)
        elif static_attribute(attribute_type, "__get__") is not _ABSENT:
            omitted_names.append(name)
        elif callable(attribute):
            method_name_to_value[name] = attribute
        else:
            if is_model and (not exact_type(model_fields, dict) or name not in model_fields):
                continue
            field_name_to_value[name] = attribute
    return PublicMembers(method_name_to_value, field_name_to_value, tuple(omitted_names))


def discover_type_alias_reference_names(
    *,
    step_locals: dict[str, object],
    step_globals: dict[str, object],
    input_binding_names: Iterable[str],
) -> frozenset[str]:
    discovered_names: set[str] = set()
    seen: set[int] = set()

    def _collect(annotation: object) -> None:
        if exact_type(annotation, TypeAliasType):
            name = annotation.__name__
            if name in step_globals and name not in step_locals:
                discovered_names.add(name)
            return

        if exact_type(annotation, str):
            import ast

            if len(annotation) > 4_096:
                return
            try:
                expression = ast.parse(annotation, mode="eval")
            except SyntaxError:
                return
            for node in ast.walk(expression):
                if isinstance(node, ast.Name) and exact_type(step_globals.get(node.id), TypeAliasType) and node.id not in step_locals:
                    discovered_names.add(node.id)
            return

        annotation_id = id(annotation)
        if annotation_id in seen:
            return
        seen.add(annotation_id)

        for arg in annotation_arguments(annotation):
            _collect(arg)

    def _scan_callable(value: object) -> None:
        for annotation in describe_callable(value).annotations:
            _collect(annotation)

    for value in step_locals.values():
        if callable(value):
            _scan_callable(value)

    step_locals_keys = step_locals.keys()
    for name in input_binding_names:
        if name not in step_locals_keys and name in step_globals:
            value = step_globals[name]
            if callable(value):
                _scan_callable(value)

    return frozenset(discovered_names)
