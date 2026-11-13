from __future__ import annotations

import dataclasses
from functools import cache
from typing import cast

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool
from typing_extensions import TypeForm

from nighthawk.core.assignment import assign_expression_async, assign_structured_value_async
from nighthawk.core.expression import evaluate_expression_async
from nighthawk.core.step_context import StepContext

STRUCTURED_ASSIGNMENT_TOOL_NAME_PREFIX = "nh_assign_"


@cache
def get_provided_tools() -> tuple[Tool[StepContext], ...]:
    async def nh_assign(
        run_context: RunContext[StepContext],
        target_path: str,
        expression: str,
    ) -> dict[str, object]:
        result = await assign_expression_async(run_context.deps, target_path, expression)
        return {
            "target_path": result.target_path,
            "retained_as": result.retained_as,
            "updates": [{"path": result.target_path, "value": result.value}],
        }

    async def nh_evaluate(run_context: RunContext[StepContext], expression: str) -> object:
        return await evaluate_expression_async(run_context.deps, expression)

    return (
        Tool(
            nh_assign,
            metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "assignment"},
            description="Assign a Python expression to a declared output, a new temporary name, or a Pydantic field with assignment validation enabled. target_path: name(.field)*.",
        ),
        Tool(
            nh_evaluate,
            metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "evaluation"},
            description="Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
        ),
    )


def _has_structured_contract(contract: object) -> bool:
    return isinstance(contract, type) and (issubclass(contract, BaseModel) or dataclasses.is_dataclass(contract))


def _keeps_meaning_when_strict(schema: object) -> bool:
    """Whether a provider's strict mode, which closes every object and types every array item, can express the schema."""
    if isinstance(schema, list):
        return all(_keeps_meaning_when_strict(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "object" and (not schema.get("properties") or schema.get("additionalProperties") not in (None, False)):
        return False
    if schema.get("type") == "array" and (not schema.get("items") or "prefixItems" in schema):
        return False
    return all(_keeps_meaning_when_strict(value) for value in schema.values())


def _structured_assignment_tool(name: str, contract: type) -> Tool[StepContext]:
    async def assign(run_context: RunContext[StepContext], **field_name_to_value: object) -> dict[str, object]:
        result = await assign_structured_value_async(run_context.deps, name, field_name_to_value)
        return {"target_path": result.target_path, "retained_as": result.retained_as}

    schema = TypeAdapter(cast(TypeForm[object], contract)).json_schema()
    # Pydantic AI validates no arguments for a schema-defined tool, so the assignment boundary reports a contract failure
    # as a recoverable operation error instead of spending the tool's retry budget.
    tool: Tool[StepContext] = Tool.from_schema(
        assign,
        name=f"{STRUCTURED_ASSIGNMENT_TOOL_NAME_PREFIX}{name}",
        description=f"Assign the declared output {name} from structured arguments that follow its contract.",
        json_schema=schema,
        takes_ctx=True,
    )
    tool.strict = _keeps_meaning_when_strict(schema)
    tool.metadata = {"nighthawk.provided": True, "nighthawk.operation_kind": "assignment", "nighthawk.assignment_target": name}
    return tool


def get_structured_assignment_tools(step_context: StepContext) -> tuple[Tool[StepContext], ...]:
    """One tool for each declared output of the step whose contract is a Pydantic model or dataclass, in name order."""
    environment = step_context.environment
    return tuple(
        _structured_assignment_tool(name, cast(type, environment.binding_name_to_type[name]))
        for name in sorted(environment.output_name_set)
        if _has_structured_contract(environment.binding_name_to_type.get(name))
    )
