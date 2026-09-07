"""Resolved Python outcomes at the host commit boundary."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Pass:
    """Continue normal execution after this step."""

    kind: Literal["pass"] = field(default="pass", init=False)


@dataclass(frozen=True)
class Return:
    """Return the resolved Python value, including None."""

    value: object
    kind: Literal["return"] = field(default="return", init=False)


@dataclass(frozen=True)
class Break:
    """Exit the enclosing loop."""

    kind: Literal["break"] = field(default="break", init=False)


@dataclass(frozen=True)
class Continue:
    """Start the next enclosing loop iteration."""

    kind: Literal["continue"] = field(default="continue", init=False)


@dataclass(frozen=True)
class Raise:
    """Raise an exception, optionally selected by a Python binding name."""

    message: str
    error_type: str | None = None
    kind: Literal["raise"] = field(default="raise", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or (self.error_type is not None and not isinstance(self.error_type, str)):
            raise TypeError("Raise requires a string message and an optional error type binding name")


type StepResult = Pass | Return | Break | Continue | Raise
