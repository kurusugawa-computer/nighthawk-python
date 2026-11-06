"""Path-boundary helpers for the workspace-scoped tool capabilities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "is_inside_any",
    "resolve_inside",
    "resolve_write_target",
    "to_workspace_relative",
]


def _resolved_roots(allowed_roots: Iterable[Path]) -> list[Path]:
    return [allowed_root.resolve(strict=True) for allowed_root in allowed_roots]


def is_inside_any(candidate: Path, allowed_roots: Iterable[Path]) -> bool:
    """Return True when ``candidate`` is at or under one of ``allowed_roots``.

    ``candidate`` must already be resolved by the caller. ``allowed_roots``
    are resolved here.
    """

    return any(candidate == allowed_root or candidate.is_relative_to(allowed_root) for allowed_root in _resolved_roots(allowed_roots))


def resolve_inside(file_path: Path, allowed_roots: Iterable[Path]) -> Path:
    """Resolve ``file_path`` strictly and confirm it lies inside one root.

    Raises :class:`ValueError` for non-existent paths (`strict=True`),
    and for resolved paths outside every allow-list root.
    """

    resolved = file_path.resolve(strict=True)
    if not is_inside_any(resolved, allowed_roots):
        raise ValueError(f"path {file_path!s} is outside the configured allow-list")
    return resolved


def resolve_write_target(
    file_path: Path,
    *,
    allowed_roots: Iterable[Path],
    must_exist: bool,
) -> Path:
    """Resolve a write target with verb-aware existence semantics.

    ``must_exist=True`` mirrors :func:`resolve_inside`. ``must_exist=False``
    handles ``add`` operations: the target itself may not exist, but the
    parent directory must exist; the parent is canonicalised strictly,
    joined with the file name, and re-checked against the allow-list.
    """

    if must_exist:
        return resolve_inside(file_path, allowed_roots)
    parent = file_path.parent
    if not parent.exists():
        raise FileNotFoundError(f"parent directory of {file_path!s} does not exist")
    resolved_parent = parent.resolve(strict=True)
    resolved = resolved_parent / file_path.name
    if not is_inside_any(resolved, allowed_roots):
        raise ValueError(f"path {file_path!s} is outside the configured allow-list")
    return resolved


def to_workspace_relative(resolved_path: Path, workspace_root: Path) -> str | None:
    """Return ``resolved_path`` rebased against ``workspace_root`` if possible.

    Returns ``None`` when ``resolved_path`` is not inside
    ``workspace_root``. Callers should fall back to extension-only
    metadata when ``None`` is returned.
    """

    workspace_root_resolved = workspace_root.resolve(strict=False)
    if resolved_path == workspace_root_resolved or resolved_path.is_relative_to(workspace_root_resolved):
        return str(resolved_path.relative_to(workspace_root_resolved))
    return None
