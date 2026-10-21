"""Execute trusted promptfoo fixtures through the production embedded-step boundary."""

from __future__ import annotations

import builtins
import inspect
import json
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic_core import to_jsonable_python

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle, StepRaised
from nighthawk.natural.blocks import parse_frontmatter, validate_frontmatter_deny
from nighthawk.runtime import Return, StepDefinition, execute_step_async
from nighthawk.runtime.step_outcome import OUTCOME_KINDS


async def _async_add(first: int, second: int) -> int:
    return first + second


_FIXTURE_NAME_TO_CALLABLE: dict[str, Callable[..., object]] = {
    "add": lambda first, second: first + second,
    "async_add": _async_add,
    "multiply": lambda first, second: first * second,
    "transform_upper": lambda text: text.upper(),
    "clamp": lambda value, low, high: max(low, min(high, value)),
    "format_name": lambda first, last: f"{first} {last}",
}


def _resolve_input_binding_value(value: Any, function_calls: list[dict[str, object]]) -> Any:
    if not isinstance(value, str) or not value.startswith("__callable:"):
        return value
    fixture_name = value[len("__callable:") :]
    function = _FIXTURE_NAME_TO_CALLABLE[fixture_name]
    signature = inspect.signature(function)

    @wraps(function)
    def recorded(*arguments: object, **keyword_name_to_value: object) -> object:
        bound = signature.bind(*arguments, **keyword_name_to_value)
        bound.apply_defaults()
        function_calls.append({"name": fixture_name, "arguments": dict(bound.arguments)})
        return function(*arguments, **keyword_name_to_value)

    return recorded


def _resolve_global_bindings(raw_global_name_to_value: dict[str, Any]) -> dict[str, object]:
    return {name: getattr(builtins, name) if value == "__builtin__" else value for name, value in raw_global_name_to_value.items()}


async def _run_evaluation(options: dict, context: dict, meter: nh.UsageMeter) -> dict[str, Any]:
    test_name_to_value = context["vars"]
    # Promptfoo injects its asset base directory into every provider configuration.
    configuration = nh.StepExecutorConfiguration.model_validate({name: value for name, value in options["config"].items() if name != "basePath"})
    function_calls: list[dict[str, object]] = []
    raw_input_name_to_value = json.loads(test_name_to_value.get("input_bindings", "{}"))
    input_name_to_value = {name: _resolve_input_binding_value(value, function_calls) for name, value in raw_input_name_to_value.items()}
    output_binding_names = json.loads(test_name_to_value.get("output_binding_names", "[]"))
    global_name_to_value = _resolve_global_bindings(json.loads(test_name_to_value.get("global_bindings", "{}")))
    name_to_value = {**global_name_to_value, **input_name_to_value}

    program, frontmatter = parse_frontmatter(test_name_to_value["natural_program"])
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
        source_location=f"<promptfoo>:{context.get('test', {}).get('description', 'fixture')}",
    )
    records: list[StepFinished] = []
    output_name_to_value: dict[str, object] = {}
    output_name_to_data: dict[str, Any] = {
        "bindings": output_name_to_value,
        "step_locals": name_to_value,
        "function_calls": function_calls,
    }
    result: dict[str, Any] = {}

    def write_binding(name: str, value: object) -> None:
        output_name_to_value[name] = value
        name_to_value[name] = value

    with nh.run(step_executor_configuration=configuration, usage_meter=meter), nh.scope(lifecycle=StepLifecycle(on_step_finished=records.append)):
        try:
            outcome = await execute_step_async(definition, name_to_value=name_to_value, write_binding=write_binding)
        except BaseException as exception:
            if records and isinstance(records[-1], StepRaised) and records[-1].exception is exception:
                outcome = records[-1].outcome
            elif isinstance(exception, Exception):
                outcome = None
                output_name_to_data.update(outcome_kind="error", error=str(exception), error_type=type(exception).__name__)
                result["error"] = str(exception)
            else:
                raise
        if outcome is not None:
            output_name_to_data["outcome_kind"] = outcome.kind
            if isinstance(outcome, Return):
                output_name_to_data["return_value"] = outcome.value
            elif isinstance(records[-1], StepRaised):
                output_name_to_data["raise_message"] = records[-1].outcome.message
                output_name_to_data["raise_error_type"] = records[-1].outcome.error_type.__name__

    result["output"] = json.dumps(to_jsonable_python(output_name_to_data, fallback=str))
    return result


async def call_api(prompt: str, options: dict, context: dict) -> dict:
    """Use StepExecutorConfiguration fields from config and JSON-encoded fixture variables.

    Reports committed bindings, resolved outcomes, retained effects, attempted fixture
    calls, and completed-agent usage. Cancellation propagates to the evaluation host.
    """
    started_at = time.monotonic()
    meter = nh.UsageMeter()
    try:
        result = await _run_evaluation(options, context, meter)
    except Exception as exception:
        result = {
            "output": json.dumps({"outcome_kind": "error", "error": str(exception), "error_type": type(exception).__name__}),
            "error": str(exception),
        }
    usage = meter.snapshot()
    result["tokenUsage"] = {
        "prompt": usage.input_tokens,
        "completion": usage.output_tokens,
        "total": usage.total_tokens,
        "numRequests": usage.requests,
    }
    result["latencyMs"] = int((time.monotonic() - started_at) * 1000)
    return result
