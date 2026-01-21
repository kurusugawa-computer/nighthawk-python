from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

_workspace_root_var: ContextVar[Path | None] = ContextVar(
    "nighthawk_workspace_root",
    default=None,
)


def get_workspace_root() -> Path | None:
    return _workspace_root_var.get()


@contextmanager
def runtime_context(*, workspace_root: str | Path) -> Iterator[None]:
    resolved = Path(workspace_root).expanduser().resolve()
    token = _workspace_root_var.set(resolved)
    try:
        yield
    finally:
        _workspace_root_var.reset(token)
