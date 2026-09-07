"""Immutable invocation identity, independent of runtime configuration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionReference:
    """Run and scope identity, plus invocation and source identity during a step."""

    run_id: str
    scope_id: str
    step_execution_id: str | None = None
    source_location: str | None = None
