"""Custom promptfoo provider wrapping Nighthawk AgentStepExecutor.

Executes a Natural block via AgentStepExecutor.run_step() and returns
structured output for promptfoo assertion evaluation.

Usage in promptfooconfig.yaml:
    providers:
      - id: file://provider.py
        config:
          model: "openai-responses:gpt-5.4-mini"
          tool_preset: "baseline"  # or "eval_functional", "py_functional", etc.
          suffix_variant: "control"  # or "terse", "examples"
"""

from __future__ import annotations

import builtins
import json
import time
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

import nighthawk as nh
import nighthawk.runtime.step_executor as _step_executor_module
import nighthawk.tools.registry as _registry_module
from nighthawk.natural.blocks import parse_frontmatter, validate_frontmatter_deny
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import StepKind
from nighthawk.runtime.step_executor import AgentStepExecutor
from nighthawk.tools.assignment import assign_tool, eval_expression
from nighthawk.tools.contracts import ToolBoundaryError
from nighthawk.tools.registry import (
    ToolDefinition,
    _builtin_tool_name_to_definition,
    _reset_all_tools_for_tests,
)

# ---------------------------------------------------------------------------
# Token usage capture — monkey-patch _run_agent to store RunUsage
# ---------------------------------------------------------------------------

_original_run_agent = AgentStepExecutor._run_agent


async def _capturing_run_agent(self: AgentStepExecutor, **kwargs: Any) -> Any:  # type: ignore[override]
    result = await _original_run_agent(self, **kwargs)
    try:
        self._last_run_usage = result.usage()  # type: ignore[attr-defined]
    except Exception:
        self._last_run_usage = None  # type: ignore[attr-defined]
    return result


AgentStepExecutor._run_agent = _capturing_run_agent  # type: ignore[assignment,method-assign]

# ---------------------------------------------------------------------------
# Suffix fragment variant infrastructure
# ---------------------------------------------------------------------------

_original_suffix_builder = _step_executor_module.build_step_system_prompt_suffix_fragment


def _build_suffix_terse(
    *,
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    """Variant A — Terse: minimal prose, rely on JSON schema for structure."""
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
    allowed_kinds: tuple[StepKind, ...],
    raise_error_type_binding_names: tuple[str, ...],
) -> str:
    """Variant B — Example-per-kind: one JSON example per allowed kind."""
    sections: list[str] = [
        'StepFinalResult: output {"result": {...}} with the correct kind.\n',
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


_SUFFIX_VARIANT_BUILDERS: dict[str, Any] = {
    "terse": _build_suffix_terse,
    "examples": _build_suffix_examples,
}


def _install_suffix_variant(variant_name: str) -> None:
    """Install a suffix fragment builder variant by monkey-patching the step_executor module."""
    if variant_name == "control":
        _step_executor_module.build_step_system_prompt_suffix_fragment = _original_suffix_builder
    elif variant_name in _SUFFIX_VARIANT_BUILDERS:
        _step_executor_module.build_step_system_prompt_suffix_fragment = _SUFFIX_VARIANT_BUILDERS[variant_name]
    else:
        raise ValueError(f"Unknown suffix_variant: {variant_name!r}. Available: control, {', '.join(_SUFFIX_VARIANT_BUILDERS)}")


# ---------------------------------------------------------------------------
# Tool preset infrastructure
# ---------------------------------------------------------------------------


def _eval_expression_or_raise(run_context: RunContext[StepContext], expression: str) -> object:
    try:
        return eval_expression(run_context.deps, expression)
    except Exception as exception:
        raise ToolBoundaryError(kind="execution", message=str(exception), guidance="Fix the expression and retry.") from exception


def _build_eval_tool(*, name: str, description: str) -> Tool[StepContext]:
    """Build a Python expression evaluation tool with a given name and description."""

    def tool_function(run_context: RunContext[StepContext], expression: str) -> object:
        return _eval_expression_or_raise(run_context, expression)

    tool_function.__name__ = name
    return Tool(tool_function, name=name, metadata={"nighthawk.provided": True}, description=description)


def _build_assign_tool(*, name: str, description: str) -> Tool[StepContext]:
    """Build an assignment tool with a given name and description."""

    def tool_function(run_context: RunContext[StepContext], target_path: str, expression: str) -> dict[str, Any]:
        return assign_tool(run_context.deps, target_path, expression)

    tool_function.__name__ = name
    return Tool(tool_function, name=name, metadata={"nighthawk.provided": True}, description=description)


# Tool description variants
_EVAL_DESCRIPTIONS = {
    "functional": "Evaluate a Python expression and return the result. Use for inspecting values, calling functions, and mutating objects in-place.",
    "examples": "Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
    "mutation_hint": "Evaluate a Python expression and return the result. Use for reading values, calling functions, and mutating objects IN-PLACE (e.g., items.sort(), data.update({...})).",
}

_ASSIGN_DESCRIPTIONS = {
    "functional": "Rebind a name or set a nested field to a new value. target_path format: name(.field)*.",
    "examples": "Set a write binding to a new value. target_path: a name or dotted path (e.g., 'result', 'obj.field'). expression: evaluated as Python.",
    "mutation_hint": "Set a write binding (<:name>) to a new value. target_path: name or dotted path. expression: Python expression.",
}


def _build_tool_preset(preset_name: str) -> list[ToolDefinition]:
    """Build tool definitions for a named preset."""

    # Parse preset: {tool_name}_{description_style}
    parts = preset_name.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"Unknown tool preset: {preset_name!r}")

    tool_name_key, description_style = parts

    if tool_name_key == "eval":
        eval_name = "nh_eval"
        descriptions = _EVAL_DESCRIPTIONS
    else:
        raise ValueError(f"Unknown tool name key in preset: {tool_name_key!r}")

    if description_style not in descriptions:
        raise ValueError(f"Unknown description style in preset: {description_style!r}")

    eval_tool = _build_eval_tool(name=eval_name, description=descriptions[description_style])
    assign_tool_obj = _build_assign_tool(
        name="nh_assign",
        description=_ASSIGN_DESCRIPTIONS[description_style],
    )

    return [
        ToolDefinition(name="nh_assign", tool=assign_tool_obj),
        ToolDefinition(name=eval_name, tool=eval_tool),
    ]


def _install_tool_preset(preset_name: str) -> None:
    """Reset global tool registry and install a specific tool preset."""
    _reset_all_tools_for_tests()
    tool_definitions = _build_tool_preset(preset_name)
    for tool_definition in tool_definitions:
        _builtin_tool_name_to_definition[tool_definition.name] = tool_definition
    _registry_module._builtin_tools_registered = True  # noqa: SLF001


# ---------------------------------------------------------------------------
# Existing provider infrastructure
# ---------------------------------------------------------------------------


def _compute_allowed_step_kinds(
    denied_step_kinds: tuple[str, ...],
    *,
    is_in_loop: bool = False,
) -> tuple[str, ...]:
    """Compute allowed step kinds from denied kinds (mirrors runner._compute_allowed_step_kinds)."""
    base_allowed_kinds: list[str] = ["pass", "return", "raise"]
    if is_in_loop:
        base_allowed_kinds.extend(["break", "continue"])
    return tuple(kind for kind in base_allowed_kinds if kind not in denied_step_kinds)


def _resolve_global_bindings(raw_global_bindings: dict[str, Any]) -> dict[str, object]:
    """Resolve global binding specifications to actual Python objects.

    Special value "__builtin__" resolves the name from Python builtins.
    """
    resolved: dict[str, object] = {}
    for name, value in raw_global_bindings.items():
        if value == "__builtin__":
            builtin_value = getattr(builtins, name, None)
            if builtin_value is not None:
                resolved[name] = builtin_value
        else:
            resolved[name] = value
    return resolved


def _serialize_value(value: object) -> object:
    """Serialize a Python value to a JSON-compatible form."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(val) for key, val in value.items()}
    return str(value)


# Predefined callable fixtures for test cases that require function bindings.
# Test YAML specifies "__callable:<key>" as the value; the provider resolves it here.
_CALLABLE_FIXTURE_REGISTRY: dict[str, object] = {
    "add": lambda a, b: a + b,
    "multiply": lambda a, b: a * b,
    "transform_upper": lambda s: s.upper(),
    "clamp": lambda value, low, high: max(low, min(high, value)),
    "format_name": lambda first, last: f"{first} {last}",
}


def _resolve_input_binding_value(value: Any) -> Any:
    """Resolve special binding value markers to actual Python objects."""
    if isinstance(value, str) and value.startswith("__callable:"):
        fixture_key = value[len("__callable:") :]
        if fixture_key in _CALLABLE_FIXTURE_REGISTRY:
            return _CALLABLE_FIXTURE_REGISTRY[fixture_key]
        raise ValueError(f"Unknown callable fixture: {fixture_key!r}")
    return value


def _read_system_prompt_file(file_path: str) -> str:
    """Read a system prompt template from a file path relative to the provider directory."""
    provider_directory = Path(__file__).parent
    resolved_path = provider_directory / file_path
    return resolved_path.read_text(encoding="utf-8")


def call_api(prompt: str, options: dict, context: dict) -> dict:  # noqa: ARG001
    """promptfoo custom provider entry point.

    Executes a Nighthawk Natural block and returns structured output.

    Expected vars:
        natural_program (str): Natural block text (may include frontmatter).
        input_bindings (JSON str): Read binding name-to-value mapping.
        output_binding_names (JSON str): Write binding names list.
        global_bindings (JSON str, optional): Global name-to-value mapping.
        allowed_step_kinds (JSON str, optional): Override for allowed step kinds.

    Expected options.config:
        model (str): Model identifier (e.g. "openai-responses:gpt-5.4-mini").
        system_prompt_file (str, optional): Path to system prompt template file.
        reasoning_effort (str, optional): Reasoning effort level.
        tool_preset (str, optional): Tool preset name. Default: "eval_examples".
            Available presets:
              "eval_functional"   - 2 tools: nh_eval, nh_assign (functional descriptions)
              "eval_examples"     - 2 tools: nh_eval, nh_assign (example-based descriptions)
              "eval_mutation_hint" - 2 tools: nh_eval, nh_assign (mutation-focused descriptions)
        suffix_variant (str, optional): Suffix fragment variant. Default: "control".
            Available variants:
              "control"     - Current production suffix text (no change)
              "terse"       - Minimal prose, rely on JSON schema
              "examples"    - One concrete JSON example per allowed kind
    """
    test_variables = context.get("vars", {})
    provider_configuration = options.get("config", {})

    # -- Install tool preset --
    tool_preset = provider_configuration.get("tool_preset", "eval_examples")
    _install_tool_preset(tool_preset)
    installed_tool_names = tuple(_builtin_tool_name_to_definition.keys())

    # -- Install suffix variant --
    suffix_variant = provider_configuration.get("suffix_variant", "control")
    _install_suffix_variant(suffix_variant)

    # -- Extract test case variables --
    natural_program = test_variables["natural_program"]
    raw_input_bindings: dict[str, Any] = json.loads(test_variables.get("input_bindings", "{}"))
    input_bindings: dict[str, Any] = {name: _resolve_input_binding_value(value) for name, value in raw_input_bindings.items()}
    output_binding_names: list[str] = json.loads(test_variables.get("output_binding_names", "[]"))
    raw_global_bindings: dict[str, Any] = json.loads(test_variables.get("global_bindings", "{}"))
    allowed_step_kinds_override = test_variables.get("allowed_step_kinds", None)

    # -- Handle frontmatter and compute allowed step kinds --
    program_without_frontmatter, frontmatter = parse_frontmatter(natural_program)
    denied_step_kinds = validate_frontmatter_deny(frontmatter)

    if allowed_step_kinds_override is not None:
        allowed_step_kinds = tuple(json.loads(allowed_step_kinds_override))
    else:
        allowed_step_kinds = _compute_allowed_step_kinds(denied_step_kinds)

    processed_natural_program = program_without_frontmatter

    # -- Resolve global bindings --
    step_globals: dict[str, object] = _resolve_global_bindings(raw_global_bindings)
    step_globals["__builtins__"] = builtins.__dict__

    # -- Build configuration --
    model = provider_configuration.get("model", "openai-responses:gpt-5.4-mini")

    prompt_templates = nh.StepPromptTemplates()
    system_prompt_file = provider_configuration.get("system_prompt_file", None)
    if system_prompt_file is not None:
        system_prompt_text = _read_system_prompt_file(system_prompt_file)
        prompt_templates = nh.StepPromptTemplates(
            step_system_prompt_template=system_prompt_text,
        )

    model_settings_dict: dict[str, Any] | None = None
    if model.startswith("claude-code-cli:"):
        model_settings_dict = {
            "allowed_tool_names": installed_tool_names,
            "permission_mode": "bypassPermissions",
            "max_turns": 25,
        }
    elif model.startswith("codex:"):
        model_settings_dict = {
            "allowed_tool_names": installed_tool_names,
        }
    else:
        reasoning_effort = provider_configuration.get("reasoning_effort", None)
        if reasoning_effort is not None:
            model_settings_dict = {"openai_reasoning_effort": reasoning_effort}

    configuration = nh.StepExecutorConfiguration(
        model=model,
        model_settings=model_settings_dict,
        prompts=prompt_templates,
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=configuration,
    )

    # -- Build step context --
    read_binding_names = frozenset(name for name in input_bindings if name not in output_binding_names)
    step_locals = dict(input_bindings)

    step_context = StepContext(
        step_id="promptfoo-eval",
        step_globals=step_globals,
        step_locals=step_locals,
        binding_commit_targets=set(output_binding_names),
        read_binding_names=read_binding_names,
        implicit_reference_name_to_value={},
    )

    # -- Execute --
    start_time = time.time()
    try:
        with nh.run(step_executor):
            step_outcome, bindings = step_executor.run_step(
                processed_natural_program=processed_natural_program,
                step_context=step_context,
                binding_names=output_binding_names,
                allowed_step_kinds=allowed_step_kinds,
            )

        elapsed_milliseconds = int((time.time() - start_time) * 1000)

        # Build structured output
        output_data: dict[str, Any] = {
            "outcome_kind": step_outcome.kind,
            "bindings": {name: _serialize_value(value) for name, value in bindings.items()},
            "step_locals": {name: _serialize_value(value) for name, value in step_context.step_locals.items() if not name.startswith("__")},
        }

        # Add kind-specific fields
        if step_outcome.kind == "return":
            output_data["return_expression"] = step_outcome.return_expression  # type: ignore[union-attr]
        elif step_outcome.kind == "raise":
            output_data["raise_message"] = step_outcome.raise_message  # type: ignore[union-attr]
            output_data["raise_error_type"] = step_outcome.raise_error_type  # type: ignore[union-attr]

        # Extract token usage from the monkey-patched _run_agent
        run_usage = getattr(step_executor, "_last_run_usage", None)
        token_usage: dict[str, int] = {}
        if run_usage is not None:
            token_usage = {
                "prompt": run_usage.input_tokens,
                "completion": run_usage.output_tokens,
                "total": run_usage.input_tokens + run_usage.output_tokens,
                "numRequests": run_usage.requests,
            }

        return {
            "output": json.dumps(output_data),
            "latencyMs": elapsed_milliseconds,
            "tokenUsage": token_usage,
        }

    except Exception as exception:
        elapsed_milliseconds = int((time.time() - start_time) * 1000)
        return {
            "output": json.dumps(
                {
                    "outcome_kind": "error",
                    "error": str(exception),
                    "error_type": type(exception).__name__,
                }
            ),
            "latencyMs": elapsed_milliseconds,
            "error": str(exception),
        }
