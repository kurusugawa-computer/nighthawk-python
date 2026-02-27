from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    scope_id: str


__all__ = [
    "ExecutionContext",
]
