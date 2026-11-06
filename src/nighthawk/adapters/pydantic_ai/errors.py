from nighthawk.core.errors import NameConflictError
from nighthawk.errors import NighthawkError


class ToolDeclarationError(NighthawkError):
    """Raised when a tool declaration is invalid."""


class ToolNameConflictError(ToolDeclarationError, NameConflictError):
    """Raised when tool declarations claim the same or a reserved name."""
