from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EXECUTION_OUTCOME_TYPES: tuple[str, ...] = ("pass", "return", "break", "continue", "raise")

EXECUTION_OUTCOME_TYPES_NON_LOOP: tuple[str, ...] = ("pass", "return", "raise")


type ExecutionOutcomeType = Literal["pass", "return", "break", "continue", "raise"]


_REFERENCE_PATH_PATTERN = r"^(?!__)[A-Za-z_][A-Za-z0-9_]*(?:\.(?!__)[A-Za-z_][A-Za-z0-9_]*)*$"


class PassOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pass"]


class ReturnOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["return"]
    source_path: str


class BreakOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["break"]


class ContinueOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["continue"]


class RaiseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["raise"]
    message: str
    error_type: str | None = None


type ExecutionOutcome = Annotated[
    PassOutcome | ReturnOutcome | BreakOutcome | ContinueOutcome | RaiseOutcome,
    Field(discriminator="type"),
]


def build_execution_outcome_json_schema(
    *,
    allowed_outcome_types: tuple[ExecutionOutcomeType, ...],
    error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_outcome_types:
        raise ValueError("allowed_outcome_types must not be empty")

    properties: dict[str, object] = {
        "type": {
            "type": "string",
            "enum": list(allowed_outcome_types),
        },
    }

    if "return" in allowed_outcome_types:
        properties["source_path"] = {
            "type": "string",
            "pattern": _REFERENCE_PATH_PATTERN,
        }

    if "raise" in allowed_outcome_types:
        properties["message"] = {
            "type": "string",
        }

    if error_type_binding_names:
        properties["error_type"] = {
            "type": "string",
            "enum": list(error_type_binding_names),
        }

    schema: dict[str, object] = {
        "type": "object",
        "title": "ExecutionOutcome",
        "properties": properties,
        "required": ["type"],
        "additionalProperties": False,
    }

    # NOTE: Some providers reject schemas with combinators (oneOf/anyOf/allOf/not/if/then/else) at the top level.
    # This schema focuses on being a top-level object with a discriminator-like `type` field.
    # Additional structural validation is enforced after parsing.

    return schema
