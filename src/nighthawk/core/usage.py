from __future__ import annotations

import threading

from pydantic import BaseModel, ConfigDict, Field


class ExecutionUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_count: int = Field(default=0, ge=0)
    input_token_count: int = Field(default=0, ge=0)
    output_token_count: int = Field(default=0, ge=0)
    total_token_count: int = Field(default_factory=lambda values: values["input_token_count"] + values["output_token_count"], ge=0)

    def __add__(self, other: ExecutionUsage) -> ExecutionUsage:
        return ExecutionUsage(
            request_count=self.request_count + other.request_count,
            input_token_count=self.input_token_count + other.input_token_count,
            output_token_count=self.output_token_count + other.output_token_count,
            total_token_count=self.total_token_count + other.total_token_count,
        )

    def difference(self, earlier: ExecutionUsage) -> ExecutionUsage:
        return ExecutionUsage(
            request_count=max(0, self.request_count - earlier.request_count),
            input_token_count=max(0, self.input_token_count - earlier.input_token_count),
            output_token_count=max(0, self.output_token_count - earlier.output_token_count),
            total_token_count=max(0, self.total_token_count - earlier.total_token_count),
        )


class UsageMeter:
    """Thread-safe accumulation of observed usage; recording does not admit work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage = ExecutionUsage()

    def record(self, usage: ExecutionUsage) -> None:
        with self._lock:
            self._usage = self._usage + usage

    @property
    def total_token_count(self) -> int:
        return self.snapshot().total_token_count

    def snapshot(self) -> ExecutionUsage:
        """Return an immutable observation independent of subsequent consumption."""
        with self._lock:
            return self._usage
