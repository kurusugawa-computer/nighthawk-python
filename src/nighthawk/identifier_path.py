from __future__ import annotations


def parse_identifier_path(path: str) -> tuple[str, ...] | None:
    """Return the segments of a dotted ASCII identifier path, or ``None`` when any segment is invalid or dunder-prefixed."""
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
