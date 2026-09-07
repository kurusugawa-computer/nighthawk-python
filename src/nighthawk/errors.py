from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lifecycle import StepFailed


class NighthawkError(Exception):
    """Base exception for all Nighthawk errors."""


class NaturalParseError(NighthawkError):
    """Raised when a Natural block cannot be parsed."""


class ExecutionError(NighthawkError):
    """Runtime failure exposing its terminal record and chaining the original error.

    Internal helpers may supply a message; the runtime boundary attaches a
    StepFailed record to every outward internal execution failure.
    """

    def __init__(self, failure: StepFailed | str, *, description: str | None = None) -> None:
        self.step_failed = None if isinstance(failure, str) else failure
        message = failure if isinstance(failure, str) else str(failure.original_exception)
        super().__init__(f"{description}: {message}" if description else message)


class ToolEvaluationError(NighthawkError):
    """Raised when a tool call evaluation fails."""


class ToolValidationError(NighthawkError):
    """Raised when tool input validation fails."""


class ToolDeclarationError(NighthawkError):
    """Raised when a tool declaration is invalid."""


class NameConflictError(NighthawkError):
    """Raised when distinct declarations claim the same name."""


class ToolNameConflictError(ToolDeclarationError, NameConflictError):
    """Raised when tool declarations claim the same or a reserved name."""
