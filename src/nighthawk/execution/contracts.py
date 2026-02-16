from __future__ import annotations

import textwrap
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


def build_execution_outcome_system_prompt_suffix_fragment(
    *,
    allowed_outcome_types: tuple[ExecutionOutcomeType, ...],
    error_type_binding_names: tuple[str, ...],
) -> str:
    if not allowed_outcome_types:
        raise ValueError("allowed_outcome_types must not be empty")

    allowed_types_text = ", ".join(f"`{outcome_type}`" for outcome_type in allowed_outcome_types)

    sections: list[str] = []
    sections.append(
        textwrap.dedent(
            f"""\
            Final output (ExecutionOutcome):
            - Output exactly one JSON object and nothing else.
            - Purpose: tell the host Python runtime what control flow to take AFTER this Natural block.
            - `type` MUST be one of: {allowed_types_text}.
            - Output only the fields allowed for the chosen `type`. Do not include other keys.
            """
        )
    )

    if "pass" in allowed_outcome_types:
        sections.append(
            textwrap.dedent(
                """
                - `pass`:
                  - Use this outcome by default for normal completion.
                  - Default: choose `pass`.
                  - Output exactly: {"type": "pass"}.
                """
            )
        )

    if "return" in allowed_outcome_types:
        sections.append(
            textwrap.dedent(
                """
                - `return`:
                  - Use this ONLY when the Natural program explicitly requires an immediate Python `return` from the surrounding function.
                  - Do NOT use `return` to "return the answer". Most blocks should end with `pass`.
                  - `source_path` is required and must be a dot-separated identifier path into execution locals.
                  - If you need to return a literal, nh_assign it first, then set `source_path` to that name.
                """
            )
        )

    if "break" in allowed_outcome_types:
        sections.append(
            textwrap.dedent(
                """
                - `break`:
                  - Use this only when you must break from the surrounding Python loop immediately.
                  - Output exactly: {"type": "break"}.
                """
            )
        )

    if "continue" in allowed_outcome_types:
        sections.append(
            textwrap.dedent(
                """
                - `continue`:
                  - Use this only when you must continue the next iteration of the surrounding Python loop immediately.
                  - Output exactly: {"type": "continue"}.
                """
            )
        )

    if "raise" in allowed_outcome_types:
        sections.append(
            textwrap.dedent(
                """
                - `raise`:
                  - Use this only when you must raise an exception.
                  - `message` is required.
                  - Output keys: `type`, `message`.
                """
            )
        )

        if error_type_binding_names:
            error_type_names_text = ", ".join(f"`{name}`" for name in error_type_binding_names)
            sections.append(f"""  - Optional: `error_type`. If you include `error_type`, it MUST be one of: {error_type_names_text}.\n""")

    return "".join(sections)
