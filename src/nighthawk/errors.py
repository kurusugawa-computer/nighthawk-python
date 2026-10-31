from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lifecycle import StepFailed


class NighthawkError(Exception):
    """Base exception for Nighthawk execution, policy, and delivery failures.

    Invalid arguments may raise ordinary Python or Pydantic validation errors.
    Recoverable operation diagnostics use OperationError instead.
    """


class NaturalParseError(NighthawkError):
    """Raised when a Natural block cannot be parsed."""


class ExecutionError(NighthawkError):
    """A failed Natural block, exposing its terminal record and chaining the original exception."""

    def __init__(self, step_failed: StepFailed, *, description: str | None = None) -> None:
        self.step_failed = step_failed
        message = str(step_failed.original_exception)
        super().__init__(f"{description}: {message}" if description else message)


class ToolDeclarationError(NighthawkError):
    """Raised when a tool declaration is invalid."""


class NameConflictError(NighthawkError):
    """Raised when distinct declarations claim the same name."""


class ToolNameConflictError(ToolDeclarationError, NameConflictError):
    """Raised when tool declarations claim the same or a reserved name."""
