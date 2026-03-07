from __future__ import annotations


class NighthawkError(Exception):
    """Base exception for all Nighthawk errors."""

    pass


class NaturalParseError(NighthawkError):
    """Raised when a Natural block cannot be parsed."""

    pass


class ExecutionError(NighthawkError):
    """Raised when a Natural block execution fails."""

    pass


class ToolEvaluationError(NighthawkError):
    """Raised when a tool call evaluation fails."""

    pass


class ToolValidationError(NighthawkError):
    """Raised when tool input validation fails."""

    pass


class ToolRegistrationError(NighthawkError):
    """Raised when tool registration fails."""

    pass
