"""Resolved Python outcomes at the host commit boundary."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Outcome(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid", validate_default=True)


class Pass(_Outcome):
    """Continue normal execution after this step."""

    kind: Literal["pass"] = "pass"


class Return(_Outcome):
    """Return the resolved Python value, including None."""

    value: object
    kind: Literal["return"] = "return"

    def __init__(self, value: object, **data: Any) -> None:
        data["value"] = value
        super().__init__(**data)


class Break(_Outcome):
    """Exit the enclosing loop."""

    kind: Literal["break"] = "break"


class Continue(_Outcome):
    """Start the next enclosing loop iteration."""

    kind: Literal["continue"] = "continue"


class Raise(_Outcome):
    """Raise an exception of the resolved class after commit inspection."""

    message: str
    error_type: type[BaseException] = Exception
    kind: Literal["raise"] = "raise"

    def __init__(self, message: str, error_type: type[BaseException] = Exception, **data: Any) -> None:
        if not isinstance(message, str):
            raise TypeError("Raise requires a string message")
        if not isinstance(error_type, type) or not issubclass(error_type, BaseException):
            raise TypeError("Raise error_type must be a resolved exception class")
        data.update(message=message, error_type=error_type)
        super().__init__(**data)


type StepResult = Annotated[Pass | Return | Break | Continue | Raise, Field(discriminator="kind")]
