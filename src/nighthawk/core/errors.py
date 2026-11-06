from __future__ import annotations

from typing import TYPE_CHECKING

from nighthawk.errors import NighthawkError

if TYPE_CHECKING:
    from nighthawk.core.lifecycle import StepFailed


class NaturalParseError(NighthawkError):
    """Raised when a Natural block cannot be parsed."""


class ExecutionError(NighthawkError):
    """A failed Natural block, exposing its terminal record and chaining the original exception."""

    def __init__(self, step_failed: StepFailed, *, description: str | None = None) -> None:
        self.step_failed = step_failed
        message = str(step_failed.original_exception)
        super().__init__(f"{description}: {message}" if description else message)


class NameConflictError(NighthawkError):
    """Raised when distinct declarations claim the same name."""
