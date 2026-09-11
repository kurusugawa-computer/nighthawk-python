"""Source annotations and preparation-time contract selection."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, Unpack, get_args, get_origin, get_type_hints


class MissingAnnotation:
    """Distinguish an absent annotation from explicit object, Any, or None."""


MISSING_ANNOTATION = MissingAnnotation()


@dataclass(frozen=True)
class SourceAnnotation:
    expressions: tuple[str, ...]
    parameter_kind: Literal["positional", "keyword", "unpacked"] | None = None


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
