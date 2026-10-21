"""Custom promptfoo provider wrapping Nighthawk AgentStepExecutor.

Executes a trusted evaluation fixture through the public embedded-step boundary.
Reports committed values, resolved outcomes, and completed-run usage.

Usage in promptfooconfig.yaml:
    providers:
      - id: file://provider.py
        config:
          model: "openai-responses:gpt-5.6-luna"
          tool_preset: "eval_examples"  # or "eval_functional", "eval_mutation_hint"
          suffix_variant: "control"  # or "legacy", "examples"
"""

from __future__ import annotations

import builtins
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any
from unittest.mock import patch

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

import nighthawk as nh
import nighthawk.runtime.step_executor as _step_executor_module
import nighthawk.tools.declarations as _declarations_module
from nighthawk.lifecycle import StepFinished, StepLifecycle, StepRaised
from nighthawk.natural.blocks import parse_frontmatter, validate_frontmatter_deny
from nighthawk.runtime import Return, StepDefinition, evaluate_expression, execute_step
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_outcome import OUTCOME_KINDS, OutcomeKind
from nighthawk.tools.assignment import assign_tool

# ---------------------------------------------------------------------------
# Suffix fragment variant infrastructure
# ---------------------------------------------------------------------------

_original_suffix_builder = _step_executor_module.build_model_response_instructions


def _build_suffix_legacy(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    """Legacy production suffix text before schema-semantic shortening."""
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


def _build_suffix_examples(
    *,
    allowed_kinds: tuple[OutcomeKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    """Variant B — Example-per-kind: one JSON example per allowed kind."""
    sections: list[str] = [
        'ModelStepResponse: output {"result": {...}} with the correct kind.\n',
        "Examples:",
    ]

    if "pass" in allowed_kinds:
        sections.append('- {"result": {"kind": "pass"}}')
    if "return" in allowed_kinds:
        sections.append('- {"result": {"kind": "return", "return_expression": "x"}}')
    if "break" in allowed_kinds:
        sections.append('- {"result": {"kind": "break"}}')
    if "continue" in allowed_kinds:
        sections.append('- {"result": {"kind": "continue"}}')
    if "raise" in allowed_kinds:
        raise_example = '- {"result": {"kind": "raise", "raise_message": "..."'
        if raise_error_type_binding_names:
            raise_example += f', "raise_error_type": "{raise_error_type_binding_names[0]}"'
        raise_example += "}}"
        sections.append(raise_example)

    sections.append("")
    sections.append("Default to pass unless the program explicitly requires another kind.")

    if raise_error_type_binding_names:
        error_type_names_text = ", ".join(raise_error_type_binding_names)
        sections.append(f"raise_error_type must be one of: {error_type_names_text}.")

    return "\n".join(sections) + "\n"


_SUFFIX_NAME_TO_BUILDER: dict[str, Any] = {
    "legacy": _build_suffix_legacy,
    "examples": _build_suffix_examples,
}


# ---------------------------------------------------------------------------
# Tool preset infrastructure
# ---------------------------------------------------------------------------


def _build_evaluation_tool(*, name: str, description: str) -> Tool[StepContext]:
    """Build a Python expression evaluation tool with a given name and description."""

    def tool_function(run_context: RunContext[StepContext], expression: str) -> object:
        return evaluate_expression(run_context.deps, expression)

    tool_function.__name__ = name
    return Tool(tool_function, name=name, metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "evaluation"}, description=description)


def _build_assign_tool(*, name: str, description: str) -> Tool[StepContext]:
    """Build an assignment tool with a given name and description."""

    def tool_function(run_context: RunContext[StepContext], target_path: str, expression: str) -> dict[str, Any]:
        return assign_tool(run_context.deps, target_path, expression)

    tool_function.__name__ = name
    return Tool(tool_function, name=name, metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "assignment"}, description=description)


# Tool description variants
_EVALUATION_STYLE_TO_DESCRIPTION = {
    "functional": "Evaluate a Python expression and return the result. Use for inspecting values, calling functions, and mutating objects in-place.",
    "examples": "Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
    "mutation_hint": "Evaluate a Python expression and return the result. Use for reading values, calling functions, and mutating objects IN-PLACE (e.g., items.sort(), data.update({...})).",
}

_ASSIGNMENT_STYLE_TO_DESCRIPTION = {
    "functional": "Rebind a name or set a nested field to a new value. target_path format: name(.field)*.",
    "examples": "Set a write binding to a new value. target_path: a name or dotted path (e.g., 'result', 'obj.field'). expression: evaluated as Python.",
    "mutation_hint": "Set a write binding (<:name>) to a new value. target_path: name or dotted path. expression: Python expression.",
}


def _build_tool_preset(preset_name: str) -> dict[str, Tool[StepContext]]:
    """Build the built-in tool mapping (name to tool) for a named preset."""

    # Parse preset: {tool_name}_{description_style}
    parts = preset_name.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Unknown tool preset: {preset_name!r}")

    tool_name_key, description_style = parts

    if tool_name_key == "eval":
        evaluation_name = "nh_evaluate"
        style_to_description = _EVALUATION_STYLE_TO_DESCRIPTION
    else:
        raise ValueError(f"Unknown tool name key in preset: {tool_name_key!r}")

    if description_style not in style_to_description:
        raise ValueError(f"Unknown description style in preset: {description_style!r}")

    evaluation_tool = _build_evaluation_tool(name=evaluation_name, description=style_to_description[description_style])
    assignment_tool = _build_assign_tool(
        name="nh_assign",
        description=_ASSIGNMENT_STYLE_TO_DESCRIPTION[description_style],
    )

    return {
        "nh_assign": assignment_tool,
        evaluation_name: evaluation_tool,
    }


_VARIANT_LOCK = RLock()


@contextmanager
def _evaluation_variants(tool_preset: str, suffix_variant: str) -> Iterator[tuple[str, ...]]:
    """Keep experimental module replacements local to one serialized evaluation."""
    tool_name_to_tool = _build_tool_preset(tool_preset)
    if suffix_variant == "control":
        builder = _original_suffix_builder
    else:
        try:
            builder = _SUFFIX_NAME_TO_BUILDER[suffix_variant]
        except KeyError as exception:
            raise ValueError(f"Unknown suffix_variant: {suffix_variant!r}") from exception
    with (
        _VARIANT_LOCK,
        patch.object(_step_executor_module, "build_model_response_instructions", builder),
        patch.object(_declarations_module, "_builtin_tool_name_to_tool", tool_name_to_tool),
        patch.object(_declarations_module, "_builtin_tools_registered", True),
    ):
        yield tuple(tool_name_to_tool)


def _resolve_global_bindings(raw_global_name_to_value: dict[str, Any]) -> dict[str, object]:
    """Resolve global binding specifications to actual Python objects.

    Special value "__builtin__" resolves the name from Python builtins.
    """
    resolved_name_to_value: dict[str, object] = {}
    for name, value in raw_global_name_to_value.items():
        if value == "__builtin__":
            builtin_value = getattr(builtins, name, None)
            if builtin_value is not None:
                resolved_name_to_value[name] = builtin_value
        else:
            resolved_name_to_value[name] = value
    return resolved_name_to_value


def _serialize_value(value: object) -> object:
    """Serialize a Python value to a JSON-compatible form."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(value) for key, value in value.items()}
    return str(value)


# Predefined callable fixtures for test cases that require function bindings.
# Test YAML specifies "__callable:<key>" as the value; the provider resolves it here.
_FIXTURE_NAME_TO_CALLABLE: dict[str, object] = {
    "add": lambda first, second: first + second,
    "multiply": lambda first, second: first * second,
    "transform_upper": lambda text: text.upper(),
    "clamp": lambda value, low, high: max(low, min(high, value)),
    "format_name": lambda first, last: f"{first} {last}",
}


def _resolve_input_binding_value(value: Any) -> Any:
    """Resolve special binding value markers to actual Python objects."""
    if isinstance(value, str) and value.startswith("__callable:"):
        fixture_key = value[len("__callable:") :]
        if fixture_key in _FIXTURE_NAME_TO_CALLABLE:
            return _FIXTURE_NAME_TO_CALLABLE[fixture_key]
        raise ValueError(f"Unknown callable fixture: {fixture_key!r}")
    return value


def _read_system_prompt_file(file_path: str) -> str:
    """Read a system prompt template from a file path relative to the provider directory."""
    provider_directory = Path(__file__).parent
    resolved_path = provider_directory / file_path
    return resolved_path.read_text(encoding="utf-8")


def _run_evaluation(options: dict, context: dict) -> dict[str, Any]:
    test_name_to_value = context.get("vars", {})
    configuration_name_to_value = options.get("config", {})
    natural_program = test_name_to_value["natural_program"]
    raw_input_name_to_value = json.loads(test_name_to_value.get("input_bindings", "{}"))
    input_name_to_value = {name: _resolve_input_binding_value(value) for name, value in raw_input_name_to_value.items()}
    output_binding_names = json.loads(test_name_to_value.get("output_binding_names", "[]"))
    global_name_to_value = _resolve_global_bindings(json.loads(test_name_to_value.get("global_bindings", "{}")))
    name_to_value = {**global_name_to_value, **input_name_to_value}

    program, frontmatter = parse_frontmatter(natural_program)
    denied_outcome_kinds = set(validate_frontmatter_deny(frontmatter))
    allowed_override = test_name_to_value.get("allowed_outcome_kinds")
    is_in_loop = False
    if allowed_override is not None:
        allowed_outcome_kinds = json.loads(allowed_override)
        if (
            not isinstance(allowed_outcome_kinds, list)
            or not allowed_outcome_kinds
            or any(kind not in OUTCOME_KINDS for kind in allowed_outcome_kinds)
        ):
            raise ValueError("allowed_outcome_kinds must be a nonempty list of supported outcome kinds")
        is_in_loop = any(kind in allowed_outcome_kinds for kind in ("break", "continue"))
        denied_outcome_kinds.update(kind for kind in OUTCOME_KINDS if kind not in allowed_outcome_kinds)
    if denied_outcome_kinds:
        program = "---\ndeny: " + json.dumps(sorted(denied_outcome_kinds)) + "\n---\n" + program

    definition = StepDefinition(
        program,
        input_binding_names=tuple(name_to_value),
        output_binding_names=output_binding_names,
        is_in_loop=is_in_loop,
        source_location="<promptfoo>:0",
    )
    templates = nh.StepPromptTemplates()
    if system_prompt_file := configuration_name_to_value.get("system_prompt_file"):
        templates = nh.StepPromptTemplates(system_prompt_template=_read_system_prompt_file(system_prompt_file))
    model = configuration_name_to_value.get("model", "openai-responses:gpt-5.6-luna")
    tool_preset = configuration_name_to_value.get("tool_preset", "eval_examples")
    suffix_variant = configuration_name_to_value.get("suffix_variant", "control")
    meter = nh.UsageMeter()
    records: list[StepFinished] = []
    output_name_to_value: dict[str, object] = {}

    def write_binding(name: str, value: object) -> None:
        output_name_to_value[name] = value
        name_to_value[name] = value

    with _evaluation_variants(tool_preset, suffix_variant) as installed_tool_names:
        setting_name_to_value: dict[str, Any] = {}
        if model.startswith("claude-code-cli:"):
            setting_name_to_value = {"allowed_tool_names": installed_tool_names, "permission_mode": "bypassPermissions", "max_turns": 25}
        elif model.startswith("codex:"):
            setting_name_to_value = {"allowed_tool_names": installed_tool_names}
        elif reasoning_effort := configuration_name_to_value.get("reasoning_effort"):
            setting_name_to_value = {"openai_reasoning_effort": reasoning_effort}
        configuration = nh.StepExecutorConfiguration(execution_model=model, execution_model_settings=setting_name_to_value, prompts=templates)
        with nh.run(step_executor_configuration=configuration, usage_meter=meter), nh.scope(lifecycle=StepLifecycle(on_step_finished=records.append)):
            try:
                outcome = execute_step(definition, name_to_value=name_to_value, write_binding=write_binding)
            except BaseException as exception:
                if not records or not isinstance(records[-1], StepRaised) or records[-1].exception is not exception:
                    raise
                outcome = records[-1].outcome

    output_name_to_data: dict[str, Any] = {
        "outcome_kind": outcome.kind,
        "bindings": {name: _serialize_value(value) for name, value in output_name_to_value.items()},
        "step_locals": {name: _serialize_value(value) for name, value in name_to_value.items()},
    }
    if isinstance(outcome, Return):
        output_name_to_data["return_value"] = _serialize_value(outcome.value)
    elif isinstance(records[-1], StepRaised):
        output_name_to_data["raise_message"] = records[-1].outcome.message
        output_name_to_data["raise_error_type"] = records[-1].outcome.error_type.__name__
    usage = meter.snapshot()
    return {
        "output": json.dumps(output_name_to_data),
        "tokenUsage": {"prompt": usage.input_tokens, "completion": usage.output_tokens, "total": usage.total_tokens, "numRequests": usage.requests},
    }


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """Run one trusted promptfoo fixture through validation, writeback, and terminal delivery.

    ``context["vars"]`` supplies natural_program and JSON-encoded input_bindings,
    output_binding_names, optional global_bindings, and optional allowed_outcome_kinds.
    ``options["config"]`` selects model, reasoning_effort, system_prompt_file,
    tool_preset (eval_examples, eval_functional, eval_mutation_hint), and suffix_variant (control, legacy, examples).
    Return values are resolved Python values, and DSL Raise is distinct from a runtime error.
    ``step_locals`` reports the explicit namespace after writes and in-place effects, excluding private runtime temporaries.
    Experimental tool and suffix replacements are restored after each evaluation; calls in this process are serialized.
    """
    started_at = time.monotonic()
    try:
        result = _run_evaluation(options, context)
    except Exception as exception:
        result = {
            "output": json.dumps({"outcome_kind": "error", "error": str(exception), "error_type": type(exception).__name__}),
            "error": str(exception),
        }
    result["latencyMs"] = int((time.monotonic() - started_at) * 1000)
    return result
