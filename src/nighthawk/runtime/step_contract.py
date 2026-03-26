from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

type StepKind = Literal["pass", "return", "break", "continue", "raise"]

STEP_KINDS: tuple[StepKind, ...] = get_args(StepKind.__value__)


class PassStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pass"]


class ReturnStepOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["return"]
    return_expression: str


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


# This union is used for host-side parsing after unwrapping the StepFinalResult envelope.

type StepOutcome = Annotated[
    PassStepOutcome | ReturnStepOutcome | BreakStepOutcome | ContinueStepOutcome | RaiseStepOutcome,
    Field(discriminator="kind"),
]


# NOTE: StepFinalResult is an envelope that wraps StepOutcome for LLM structured output.
# OpenAI / Codex structured outputs require the root JSON schema to be a single object (no anyOf at root level).
# StepOutcome is a discriminated union whose JSON schema produces anyOf at root, which is rejected by these providers.
# By placing the union inside a ``result`` field, the anyOf moves to a nested level where it is accepted.
# Additionally, each variant uses ``additionalProperties: false`` so that kind-specific properties are enforced (e.g. ``kind: "return"`` cannot include ``raise_message``).


class StepFinalResult(BaseModel):
    """Envelope that wraps StepOutcome for LLM structured output."""

    model_config = ConfigDict(extra="forbid")

    result: Annotated[
        PassStepOutcome | ReturnStepOutcome | BreakStepOutcome | ContinueStepOutcome | RaiseStepOutcome,
        Field(discriminator="kind"),
    ]


def _build_pass_variant_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "pass"},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def _build_return_variant_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "return"},
            "return_expression": {"type": "string"},
        },
        "required": ["kind", "return_expression"],
        "additionalProperties": False,
    }


def _build_break_variant_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "break"},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def _build_continue_variant_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "continue"},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def _build_raise_variant_schema(
    *,
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    properties: dict[str, object] = {
        "kind": {"type": "string", "const": "raise"},
        "raise_message": {"type": "string"},
    }
    required: list[str] = ["kind", "raise_message"]

    if raise_error_type_binding_names:
        properties["raise_error_type"] = {
            "type": "string",
            "enum": list(raise_error_type_binding_names),
        }

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_variant_schema(
    kind: StepKind,
    *,
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    match kind:
        case "pass":
            return _build_pass_variant_schema()
        case "return":
            return _build_return_variant_schema()
        case "break":
            return _build_break_variant_schema()
        case "continue":
            return _build_continue_variant_schema()
        case "raise":
            return _build_raise_variant_schema(raise_error_type_binding_names=raise_error_type_binding_names)


def build_step_json_schema(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    variants: list[dict[str, object]] = [
        _build_variant_schema(kind, raise_error_type_binding_names=raise_error_type_binding_names) for kind in allowed_kinds
    ]

    if len(variants) == 1:
        result_schema: dict[str, object] = variants[0]
    else:
        result_schema = {"anyOf": variants}

    schema: dict[str, object] = {
        "type": "object",
        "title": "StepFinalResult",
        "properties": {"result": result_schema},
        "required": ["result"],
        "additionalProperties": False,
    }

    return schema


def build_step_system_prompt_suffix_fragment(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    allowed_kinds_text = " | ".join(allowed_kinds)

    lines: list[str] = [
        'Output {"result": {"kind": "<kind>", ...}}.',
        f"kind: {allowed_kinds_text}. Default: pass.",
    ]

    if "pass" in allowed_kinds:
        lines.append("Choose pass after completing the work. Most blocks end with pass.")

    if "return" in allowed_kinds:
        lines.append(
            'return needs return_expression (a Python expression evaluated against step locals/globals, e.g. "result", "\'hello\'", "len(items)").'
        )

    if "raise" in allowed_kinds:
        raise_line = "raise needs raise_message."
        if raise_error_type_binding_names:
            error_type_names_text = " | ".join(raise_error_type_binding_names)
            raise_line += f" Optional raise_error_type: {error_type_names_text}."
        lines.append(raise_line)

    return "\n".join(lines) + "\n"
