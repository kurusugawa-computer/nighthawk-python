from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

STEP_KINDS: tuple[str, ...] = ("pass", "return", "break", "continue", "raise")

STEP_KINDS_NON_LOOP: tuple[str, ...] = ("pass", "return", "raise")


type StepKind = Literal["pass", "return", "break", "continue", "raise"]


_REFERENCE_PATH_PATTERN = r"^(?!__)[A-Za-z_][A-Za-z0-9_]*(?:\.(?!__)[A-Za-z_][A-Za-z0-9_]*)*$"


class PassStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pass"]


class ReturnStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["return"]
    return_reference_path: str


class BreakStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["break"]


class ContinueStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["continue"]


class RaiseStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["raise"]
    raise_message: str
    raise_error_type: str | None = None


# This union is used both for host-side parsing and for providing a JSON schema to the LLM.
# The JSON schema builder uses a top-level object with a discriminator-like `kind`.

type StepOutcome = Annotated[
    PassStepOutcome | ReturnStepOutcome | BreakStepOutcome | ContinueStepOutcome | RaiseStepOutcome,
    Field(discriminator="kind"),
]


def build_step_json_schema(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    properties: dict[str, object] = {
        "kind": {
            "type": "string",
            "enum": list(allowed_kinds),
        },
    }

    if "return" in allowed_kinds:
        properties["return_reference_path"] = {
            "type": "string",
            "pattern": _REFERENCE_PATH_PATTERN,
        }

    if "raise" in allowed_kinds:
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
        "title": "StepOutcome",
        "properties": properties,
        "required": ["kind"],
        "additionalProperties": False,
    }

    # NOTE: Some providers reject schemas with combinators (oneOf/anyOf/allOf/not/if/then/else) at the top level.
    # This schema focuses on being a top-level object with a discriminator-like `kind` field.
    # Additional structural validation is enforced after parsing.

    return schema


def build_step_system_prompt_suffix_fragment(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    allowed_kinds_text = ", ".join(f"`{outcome_kind}`" for outcome_kind in allowed_kinds)

    sections: list[str] = []
    sections.append(f"StepOutcome — output exactly one JSON object. `kind` must be one of: {allowed_kinds_text}.\n")

    if "pass" in allowed_kinds:
        sections.append(
            '\nDefault: {"kind": "pass"}\n'
            "Choose pass after completing the work. Most blocks end with pass.\n"
        )

    alternatives: list[str] = []

    if "return" in allowed_kinds:
        alternatives.append(
            "- return: immediately return from the Python function.\n"
            '  return_reference_path (required): name in step locals holding the value.\n'
            '  Example: after nh_assign("x", "42"), output {"kind": "return", "return_reference_path": "x"}.\n'
        )

    if "break" in allowed_kinds:
        alternatives.append(
            "- break: break from the surrounding Python loop.\n"
        )

    if "continue" in allowed_kinds:
        alternatives.append(
            "- continue: continue to the next loop iteration.\n"
        )

    if "raise" in allowed_kinds:
        raise_text = (
            "- raise: raise a Python exception.\n"
            "  raise_message: required.\n"
        )
        if raise_error_type_binding_names:
            error_type_names_text = ", ".join(f"`{name}`" for name in raise_error_type_binding_names)
            raise_text += f"  raise_error_type: optional, must be one of: {error_type_names_text}.\n"
        alternatives.append(raise_text)

    if alternatives:
        sections.append("\nAlternatives (use only when the program text explicitly requires it):\n")
        sections.extend(alternatives)

    return "".join(sections)
