"""Source annotations and preparation-time contract selection."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, Unpack, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter

from .bindings import MISSING_ANNOTATION


@dataclass(frozen=True)
class SourceAnnotation:
    expressions: tuple[str, ...]
    parameter_kind: Literal["positional", "keyword", "unpacked"] | None = None


def module_annotations(python_globals: dict[str, object], *, resolve_deferred: bool) -> dict[str, object]:
    stored = python_globals.get("__annotations__")
    if isinstance(stored, dict):
        return dict(stored)
    if resolve_deferred and sys.version_info >= (3, 14):
        import annotationlib

        annotate = python_globals.get("__annotate__")
        if callable(annotate):
            # This is mandatory global-output contract acquisition during
            # preparation, never presentation-only metadata discovery.
            resolved = annotationlib.call_annotate_function(annotate, annotationlib.Format.VALUE)
            if not isinstance(resolved, dict):
                raise TypeError("Module annotation metadata must be a dictionary")
            return resolved
    return {}


def resolve_annotation(
    annotation: object,
    *,
    python_globals: dict[str, object],
    python_locals: dict[str, object],
) -> object:
    if not isinstance(annotation, SourceAnnotation):
        return annotation
    if len(annotation.expressions) != 1:
        raise TypeError(f"Ambiguous annotation declarations: {annotation.expressions!r}")
    holder = SimpleNamespace(__annotations__={"value": annotation.expressions[0]})
    resolved = get_type_hints(holder, globalns=python_globals, localns=python_locals, include_extras=True)["value"]
    if annotation.parameter_kind in (None, "unpacked"):
        return resolved
    # Parameter annotations describe individual arguments; the local binding
    # contains their collection. Unpack already specifies that collection.
    if get_origin(resolved) is Unpack:
        return get_args(resolved)[0]
    if annotation.parameter_kind == "positional":
        return tuple[resolved, ...]
    return dict[str, resolved]


def resolve_contract(annotation: object, *, python_globals: dict[str, object], python_locals: dict[str, object]) -> object:
    """Resolve a contract in its selected namespace before executor admission."""
    if annotation is MISSING_ANNOTATION:
        return object
    if isinstance(annotation, SourceAnnotation):
        resolved = resolve_annotation(annotation, python_globals=python_globals, python_locals=python_locals)
    else:
        holder = SimpleNamespace(__annotations__={"value": annotation})
        resolved = get_type_hints(holder, globalns=python_globals, localns=python_locals, include_extras=True)["value"]
    TypeAdapter(resolved)
    return resolved
