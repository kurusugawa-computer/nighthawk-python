from __future__ import annotations


class NighthawkError(Exception):
    """Base exception for all Nighthawk errors."""


class NaturalParseError(NighthawkError):
    """Raised when a Natural block cannot be parsed."""


class ExecutionError(NighthawkError):
    """Raised when a Natural block execution fails."""


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
