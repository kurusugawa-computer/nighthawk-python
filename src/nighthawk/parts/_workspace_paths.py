"""Path-boundary helpers for the workspace-scoped capabilities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def _resolved_roots(allowed_roots: Iterable[Path]) -> list[Path]:
    return [allowed_root.resolve(strict=True) for allowed_root in allowed_roots]


def is_inside_any(candidate: Path, allowed_roots: Iterable[Path]) -> bool:
    """Require a caller-resolved ``candidate``; only ``allowed_roots`` are resolved here."""

    return any(candidate == allowed_root or candidate.is_relative_to(allowed_root) for allowed_root in _resolved_roots(allowed_roots))


def resolve_inside(file_path: Path, allowed_roots: Iterable[Path]) -> Path:
    resolved = file_path.resolve(strict=True)
    if not is_inside_any(resolved, allowed_roots):
        raise ValueError(f"path {file_path!s} is outside the configured allow-list")
    return resolved


def to_workspace_relative(resolved_path: Path, workspace_root: Path) -> str | None:
    workspace_root_resolved = workspace_root.resolve(strict=False)
    if resolved_path == workspace_root_resolved or resolved_path.is_relative_to(workspace_root_resolved):
        return str(resolved_path.relative_to(workspace_root_resolved))
    return None
