from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel


def default_include_roots() -> tuple[str, ...]:
    return ("docs/", "tests/")


@dataclass(frozen=True)
class Configuration:
    model: str = "gpt-5.2"
    include_roots: tuple[str, ...] = default_include_roots()
    memory_factory: Callable[[], BaseModel] | None = None

    def create_memory(self) -> BaseModel | None:
        if self.memory_factory is None:
            return None
        return self.memory_factory()
