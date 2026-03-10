"""Shared identifier path parsing and validation.

An identifier path is a dot-separated sequence of ASCII Python identifiers
where no segment starts with ``__`` (dunder).  Examples: ``result``,
``model.name``, ``config.db.host``.
"""

from __future__ import annotations


def parse_identifier_path(path: str) -> tuple[str, ...] | None:
    """Parse a dot-separated identifier path.

    Returns a tuple of path segments on success, or ``None`` if the path is
    empty, contains empty segments, non-ASCII characters, non-identifier
    segments, or dunder-prefixed segments.
    """
    if not path:
        return None

    parts = path.split(".")
    if any(part == "" for part in parts):
        return None

    for part in parts:
        try:
            part.encode("ascii")
        except UnicodeEncodeError:
            return None
        if not part.isidentifier():
            return None
        if part.startswith("__"):
            return None

    return tuple(parts)
