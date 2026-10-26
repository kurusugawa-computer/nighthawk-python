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


class ModelStepResponse(BaseModel):
    """Envelope that wraps ModelOutcome for LLM structured output."""

    model_config = ConfigDict(extra="forbid")

    result: ModelOutcome


_kind_to_model: dict[OutcomeKind, type[BaseModel]] = {
    "pass": ModelPass,
    "return": ModelReturn,
    "break": ModelBreak,
    "continue": ModelContinue,
    "raise": ModelRaise,
}


def build_model_response_schema(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    variants = []
    for kind in allowed_kinds:
        variant = _kind_to_model[kind].model_json_schema()
        if kind == "raise":
            if raise_error_type_binding_names:
                variant["properties"]["raise_error_type"] = {
                    "type": ["string", "null"],
                    "enum": [*raise_error_type_binding_names, None],
                }
            else:
                del variant["properties"]["raise_error_type"]
        variants.append(variant)

    if len(variants) == 1:
        result_schema: dict[str, object] = variants[0]
    else:
        result_schema = {"anyOf": variants}

    # Structured-output providers require an object root, even for multiple outcomes.
    return {
        "type": "object",
        "title": "ModelStepResponse",
        "properties": {"result": result_schema},
        "required": ["result"],
        "additionalProperties": False,
    }


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
            raise_line += (
                f" Use raise_error_type to select the requested exception binding: {error_type_names_text}. Null or omission uses Exception."
            )
        lines.append(raise_line)

    return "\n".join(lines) + "\n"
