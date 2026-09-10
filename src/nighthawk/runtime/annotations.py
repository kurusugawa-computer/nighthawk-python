"""Source annotations and preparation-time contract selection."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import get_type_hints


class MissingAnnotation:
    """Distinguish an absent annotation from explicit object, Any, or None."""


MISSING_ANNOTATION = MissingAnnotation()


@dataclass(frozen=True)
class SourceAnnotation:
    expressions: tuple[str, ...]


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
    return get_type_hints(holder, globalns=python_globals, localns=python_locals, include_extras=True)["value"]
