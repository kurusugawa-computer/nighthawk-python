"""Model-proposed control flow, before Python expressions have been evaluated.

These transport schemas describe execute responses, not committed Python values.
The runtime resolves a ModelOutcome into a StepCandidate with a StepOutcome.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .step_outcome import OutcomeKind


class ModelPass(BaseModel):
    """Propose continuing with the next Python statement."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pass"]


class ModelReturn(BaseModel):
    """Propose a Python expression to evaluate once as the return value."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["return"]
    return_expression: str


class ModelBreak(BaseModel):
    """Propose exiting the enclosing Python loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["break"]


class ModelContinue(BaseModel):
    """Propose starting the next iteration of the enclosing Python loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["continue"]


class ModelRaise(BaseModel):
    """Propose an exception message and an optional exception-type expression."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["raise"]
    raise_message: str
    raise_error_type: str | None = None


type ModelOutcome = Annotated[
    ModelPass | ModelReturn | ModelBreak | ModelContinue | ModelRaise,
    Field(discriminator="kind"),
]


# OpenAI / Codex structured outputs require the root JSON schema to be a single object (no anyOf at root level).
# ModelOutcome is a discriminated union whose JSON schema produces anyOf at root, which is rejected by these providers.
# By placing the union inside a ``result`` field, the anyOf moves to a nested level where it is accepted.
# Additionally, each variant uses ``additionalProperties: false`` so that kind-specific properties are enforced (e.g. ``kind: "return"`` cannot include ``raise_message``).


class ModelStepResponse(BaseModel):
    """Envelope that wraps ModelOutcome for LLM structured output."""

    model_config = ConfigDict(extra="forbid")

    result: ModelOutcome


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
    kind: OutcomeKind,
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


def build_model_response_schema(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
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
        "title": "ModelStepResponse",
        "properties": {"result": result_schema},
        "required": ["result"],
        "additionalProperties": False,
    }

    return schema


def build_model_response_instructions(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    allowed_kinds_text = " | ".join(allowed_kinds)

    lines: list[str] = ['Output {"result": {"kind": "<kind>", ...}}.']

    kind_line = f"kind: {allowed_kinds_text}."
    if "pass" in allowed_kinds:
        kind_line += " Default: pass."
    lines.append(kind_line)

    if "pass" in allowed_kinds:
        lines.append("Choose pass after completing the work. Most blocks end with pass.")

    if "break" in allowed_kinds and "continue" in allowed_kinds:
        lines.append("Use continue for instructions like continue or next iteration; use break for instructions like break or stop the loop.")
    elif "break" in allowed_kinds:
        lines.append("Use break for instructions like break or stop the loop.")
    elif "continue" in allowed_kinds:
        lines.append("Use continue for instructions like continue or next iteration.")

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
