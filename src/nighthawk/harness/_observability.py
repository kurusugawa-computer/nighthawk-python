"""Module-private observability helpers for ``nighthawk.harness``."""

from __future__ import annotations

import logging
from collections.abc import Generator, Mapping
from contextlib import contextmanager

from opentelemetry import trace

_LOGGER = logging.getLogger("nighthawk")
_TRACER = trace.get_tracer("nighthawk.harness")

type _AttributeValue = bool | int | float | str


def _clean_attributes(attributes: Mapping[str, object] | None) -> dict[str, _AttributeValue]:
    if attributes is None:
        return {}
    cleaned: dict[str, _AttributeValue] = {}
    for name, value in attributes.items():
        if isinstance(value, bool | int | float | str):
            cleaned[name] = value
        elif value is not None:
            cleaned[name] = repr(value)
    return cleaned


@contextmanager
def harness_span(name: str, attributes: Mapping[str, object] | None = None) -> Generator[None]:
    """Open an OpenTelemetry span for a Harness or Agent Part operation."""

    with _TRACER.start_as_current_span(
        f"nighthawk.harness.{name}",
        attributes=_clean_attributes(attributes),
    ):
        yield


def emit_harness_event(name: str, attributes: Mapping[str, object] | None = None) -> None:
    """Emit a post-commit observation event without affecting control flow."""

    cleaned_attributes = _clean_attributes(attributes)
    trace.get_current_span().add_event(
        f"nighthawk.harness.{name}",
        attributes=cleaned_attributes,
    )
    _LOGGER.info(
        "nighthawk.harness.%s",
        name,
        extra={"nighthawk_harness": cleaned_attributes},
    )
