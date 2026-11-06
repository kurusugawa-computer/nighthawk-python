"""Typed Session lifecycle, lookup, and presentation contracts."""

__all__ = ["SessionBusyError", "SessionClosedError", "SessionError", "SessionLookupError", "SessionWaitContextError"]


class SessionError(Exception):
    """Base error for the embedding API."""


class SessionBusyError(SessionError):
    """Another unfinished cell or wait driver owns this Session."""


class SessionClosedError(SessionError):
    """The Session is disposed or refuses submission during closing."""


class SessionLookupError(SessionError, LookupError):
    """The requested name or run is unavailable in this Session and scope."""


class SessionWaitContextError(SessionError):
    """The requested driver cannot run on the current event-loop context."""
