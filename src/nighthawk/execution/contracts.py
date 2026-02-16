from __future__ import annotations

import textwrap
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EXECUTION_OUTCOME_KINDS: tuple[str, ...] = ("pass", "return", "break", "continue", "raise")

EXECUTION_OUTCOME_KINDS_NON_LOOP: tuple[str, ...] = ("pass", "return", "raise")


type ExecutionOutcomeKind = Literal["pass", "return", "break", "continue", "raise"]


_REFERENCE_PATH_PATTERN = r"^(?!__)[A-Za-z_][A-Za-z0-9_]*(?:\.(?!__)[A-Za-z_][A-Za-z0-9_]*)*$"


class PassOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pass"]


class ReturnOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["return"]
    return_reference_path: str


class BreakOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["break"]


class ContinueOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["continue"]


class RaiseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["raise"]
    raise_message: str
    raise_error_type: str | None = None


type ExecutionOutcome = Annotated[
    PassOutcome | ReturnOutcome | BreakOutcome | ContinueOutcome | RaiseOutcome,
    Field(discriminator="kind"),
]


def build_execution_outcome_json_schema(
    *,
    allowed_outcome_kinds: tuple[ExecutionOutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_outcome_kinds:
        raise ValueError("allowed_outcome_kinds must not be empty")

    properties: dict[str, object] = {
        "kind": {
            "type": "string",
            "enum": list(allowed_outcome_kinds),
        },
    }

    if "return" in allowed_outcome_kinds:
        properties["return_reference_path"] = {
            "type": "string",
            "pattern": _REFERENCE_PATH_PATTERN,
        }

    if "raise" in allowed_outcome_kinds:
        properties["raise_message"] = {
            "type": "string",
        }

    if raise_error_type_binding_names:
        properties["raise_error_type"] = {
            "type": "string",
            "enum": list(raise_error_type_binding_names),
        }

    schema: dict[str, object] = {
        "type": "object",
        "title": "ExecutionOutcome",
        "properties": properties,
        "required": ["kind"],
        "additionalProperties": False,
    }

    # NOTE: Some providers reject schemas with combinators (oneOf/anyOf/allOf/not/if/then/else) at the top level.
    # This schema focuses on being a top-level object with a discriminator-like `kind` field.
    # Additional structural validation is enforced after parsing.

    return schema


def build_execution_outcome_system_prompt_suffix_fragment(
    *,
    allowed_outcome_kinds: tuple[ExecutionOutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    if not allowed_outcome_kinds:
        raise ValueError("allowed_outcome_kinds must not be empty")

    allowed_kinds_text = ", ".join(f"`{outcome_kind}`" for outcome_kind in allowed_outcome_kinds)

    sections: list[str] = []
    sections.append(
        textwrap.dedent(
            f"""\
            Final output (ExecutionOutcome):
            - Output exactly one JSON object and nothing else.
            - Purpose: tell the host Python runtime what control flow to take AFTER this Natural block.
            - `kind` MUST be one of: {allowed_kinds_text}.
            - Output only the fields allowed for the chosen `kind`. Do not include other keys.
            """
        )
    )

    if "pass" in allowed_outcome_kinds:
        sections.append(
            textwrap.dedent(
                """
                - `pass`:
                  - Use this outcome by default for normal completion.
                  - Default: choose `pass`.
                  - Output exactly: {"kind": "pass"}.
                """
            )
        )

    if "return" in allowed_outcome_kinds:
        sections.append(
            textwrap.dedent(
                """
                - `return`:
                  - Use this ONLY when the Natural program explicitly requires an immediate Python `return` from the surrounding function.
                  - Do NOT use `return` to "return the answer". Most blocks should end with `pass`.
                  - `return_reference_path` is required and must be a dot-separated identifier path into execution locals.
                  - If you need to return a literal, nh_assign it first, then set `return_reference_path` to that name.
                """
            )
        )

    if "break" in allowed_outcome_kinds:
        sections.append(
            textwrap.dedent(
                """
                - `break`:
                  - Use this only when you must break from the surrounding Python loop immediately.
                  - Output exactly: {"kind": "break"}.
                """
            )
        )

    if "continue" in allowed_outcome_kinds:
        sections.append(
            textwrap.dedent(
                """
                - `continue`:
                  - Use this only when you must continue the next iteration of the surrounding Python loop immediately.
                  - Output exactly: {"kind": "continue"}.
                """
            )
        )

    if "raise" in allowed_outcome_kinds:
        sections.append(
            textwrap.dedent(
                """
                - `raise`:
                  - Use this only when you must raise an exception.
                  - `raise_message` is required.
                  - Output keys: `kind`, `raise_message`.
                """
            )
        )

        if raise_error_type_binding_names:
            error_type_names_text = ", ".join(f"`{name}`" for name in raise_error_type_binding_names)
            sections.append(f"""  - Optional: `raise_error_type`. If you include `raise_error_type`, it MUST be one of: {error_type_names_text}.\n""")

    return "".join(sections)
