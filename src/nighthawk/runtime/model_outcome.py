"""Model-proposed control flow, before Python expressions have been evaluated.

These transport schemas describe execute responses, not committed Python values.
The runtime resolves a ModelOutcome into a StepCandidate with a StepOutcome.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .step_outcome import OutcomeKind


class ModelPass(BaseModel):
    """Continue with the next Python statement after completing the block."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["pass"]


class ModelReturn(BaseModel):
    """Return the value of return_expression, evaluated once in the live Python environment."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["return"]
    return_expression: str


class ModelBreak(BaseModel):
    """Exit the enclosing Python loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["break"]


class ModelContinue(BaseModel):
    """Skip to the next iteration of the enclosing Python loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["continue"]


class ModelRaise(BaseModel):
    """Raise an exception with raise_message."""

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
        del variant["title"]
        for property_schema in variant["properties"].values():
            property_schema.pop("title", None)
        if kind == "raise":
            if raise_error_type_binding_names:
                variant["properties"]["raise_error_type"] = {
                    "type": ["string", "null"],
                    "enum": [*raise_error_type_binding_names, None],
                    "description": "Exception type binding name; null selects Exception.",
                }
            else:
                del variant["properties"]["raise_error_type"]
                variant["description"] = "Raise Exception with raise_message."
        variants.append(variant)

    if len(variants) == 1:
        result_schema: dict[str, object] = variants[0]
    else:
        result_schema = {"anyOf": variants}

    # Structured-output providers require an object root, even for multiple outcomes.
    return {
        "type": "object",
        "properties": {"result": result_schema},
        "required": ["result"],
        "additionalProperties": False,
    }


def build_model_response_instructions(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
) -> str:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    if "pass" in allowed_kinds:
        return (
            'Complete the program, then submit {"result":{"kind":"pass"}} unless it requests other control flow. '
            "Assigning outputs does not return from the function."
        )
    return "Complete the program before submitting its control result."
