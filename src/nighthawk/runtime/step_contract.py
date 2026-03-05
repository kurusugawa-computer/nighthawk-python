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
            "return_reference_path": {"type": "string", "pattern": _REFERENCE_PATH_PATTERN},
        },
        "required": ["kind", "return_reference_path"],
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


_KIND_TO_VARIANT_BUILDER: dict[str, object] = {
    "pass": _build_pass_variant_schema,
    "return": _build_return_variant_schema,
    "break": _build_break_variant_schema,
    "continue": _build_continue_variant_schema,
}


def build_step_json_schema(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> dict[str, object]:
    if not allowed_kinds:
        raise ValueError("allowed_kinds must not be empty")

    variants: list[dict[str, object]] = []
    for kind in allowed_kinds:
        if kind == "raise":
            variants.append(
                _build_raise_variant_schema(
                    raise_error_type_binding_names=raise_error_type_binding_names,
                )
            )
        else:
            builder = _KIND_TO_VARIANT_BUILDER.get(kind)
            if builder is None:
                raise ValueError(f"Unknown step kind: {kind!r}")
            variants.append(builder())  # type: ignore[operator]

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

    allowed_kinds_text = ", ".join(f"`{outcome_kind}`" for outcome_kind in allowed_kinds)

    sections: list[str] = []
    sections.append(f"StepFinalResult — output exactly one JSON object with a `result` field. Inside `result`, `kind` must be one of: {allowed_kinds_text}.\n")

    if "pass" in allowed_kinds:
        sections.append('\nDefault: {"result": {"kind": "pass"}}\nChoose pass after completing the work. Most blocks end with pass.\n')

    alternatives: list[str] = []

    if "return" in allowed_kinds:
        alternatives.append('- return: immediately return from the Python function.\n  return_reference_path (required): name in step locals holding the value.\n  Example: after nh_assign("x", "42"), output {"result": {"kind": "return", "return_reference_path": "x"}}.\n')

    if "break" in allowed_kinds:
        alternatives.append("- break: break from the surrounding Python loop.\n")

    if "continue" in allowed_kinds:
        alternatives.append("- continue: continue to the next loop iteration.\n")

    if "raise" in allowed_kinds:
        raise_text = "- raise: raise a Python exception.\n  raise_message: required.\n"
        if raise_error_type_binding_names:
            error_type_names_text = ", ".join(f"`{name}`" for name in raise_error_type_binding_names)
            raise_text += f"  raise_error_type: optional, must be one of: {error_type_names_text}.\n"
        alternatives.append(raise_text)

    if alternatives:
        sections.append("\nAlternatives (use only when the program text explicitly requires it):\n")
        sections.extend(alternatives)

    return "".join(sections)
