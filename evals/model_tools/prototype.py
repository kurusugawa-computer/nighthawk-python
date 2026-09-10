"""Shared semantics for three model-facing tool surfaces.

This deliberately models step-entry environments, not Python frame capture or
production backend/lifecycle integration. All application data is synthetic.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import CodeType, MappingProxyType
from typing import Any, Literal

from pydantic import TypeAdapter
from pydantic_ai.tools import Tool

type EnvironmentSource = Literal["effective", "python", "parent", "task", "step"]
type Surface = Literal["original", "refined", "merged"]


class ApprovalRejectedError(Exception):
    """Terminal host decision, never a recoverable tool observation."""


class EvaluationNamespace(dict[str, Any]):
    def __init__(self, values: Mapping[str, Any], unbound_names: set[str]) -> None:
        super().__init__(values)
        self.unbound_names = unbound_names

    def __missing__(self, name: str) -> Any:
        if name in self.unbound_names:
            raise UnboundLocalError(f"Python local {name!r} is unbound; parent fallback is unavailable")
        raise KeyError(name)


def preview(value: object, limit: int = 280) -> str:
    """Render only synthetic scalar/container data; do not invoke user repr."""
    if type(value) in (str, int, float, bool, list, dict, tuple, type(None)):
        try:
            rendered = json.dumps(value, ensure_ascii=True)
        except (TypeError, ValueError):
            rendered = f"<{type(value).__name__}>"
    else:
        rendered = f"<{type(value).__name__}>"
    return rendered if len(rendered) <= limit else rendered[:limit] + " <snipped>"


@dataclass
class Workspace:
    python_name_to_value: dict[str, Any] = field(default_factory=dict)
    parent_name_to_value: dict[str, Any] = field(default_factory=dict)
    task_name_to_value: dict[str, Any] = field(default_factory=dict)
    step_name_to_value: dict[str, Any] = field(default_factory=dict)
    output_name_to_type: dict[str, Any] = field(default_factory=dict)
    unbound_names: set[str] = field(default_factory=set)
    operations: list[dict[str, Any]] = field(default_factory=list)
    effects: list[Any] = field(default_factory=list)
    initial_items: Any = None
    page_size: int = 4
    surface: Surface = "refined"
    revision: int = 0
    reference_root: str = ""
    cursor_to_page: dict[str, tuple[list[dict[str, Any]], int]] = field(default_factory=dict)
    function_name_to_count: dict[str, int] = field(default_factory=dict)

    def initialize(self) -> None:
        occupied = set(self.effective_values()) | self.unbound_names
        self.reference_root = "nh_environment"
        suffix = 2
        while self.reference_root in occupied:
            self.reference_root = f"nh_environment_{suffix}"
            suffix += 1

    def source_values(self, source: EnvironmentSource) -> dict[str, Any]:
        if source == "effective":
            return self.effective_values()
        return getattr(self, f"{source}_name_to_value")

    def effective_values(self) -> dict[str, Any]:
        values = {
            **self.task_name_to_value,
            **self.parent_name_to_value,
            **vars(builtins),
            **self.python_name_to_value,
        }
        for name in self.unbound_names:
            values.pop(name, None)
        values.update(self.step_name_to_value)
        return values

    def namespace(self, source: EnvironmentSource = "effective") -> EvaluationNamespace:
        values = dict(self.source_values(source))
        values["__builtins__"] = vars(builtins)
        values[self.reference_root] = MappingProxyType(
            {name: MappingProxyType(self.source_values(name)) for name in ("python", "parent", "task", "step")}
        )
        blocked = self.unbound_names - self.step_name_to_value.keys() if source in ("effective", "python") else set()
        return EvaluationNamespace(values, set(blocked))

    def origin(self, name: str) -> str:
        if name in self.step_name_to_value:
            return "step"
        if name in self.python_name_to_value or name in vars(builtins):
            return "python"
        return "parent" if name in self.parent_name_to_value else "task"

    def reference(self, source: str, name: str) -> str:
        return f"{self.reference_root}[{source!r}][{name!r}]"

    def describe_value(self, name: str, value: Any, source: str) -> dict[str, Any]:
        description: dict[str, Any] = {"name": name, "type": type(value).__name__, "preview": preview(value)}
        if callable(value):
            description["signature"] = str(inspect.signature(value))
            description["description"] = inspect.getdoc(value) or ""
            description["async"] = inspect.iscoroutinefunction(value)
        description["origin"] = self.origin(name) if source == "effective" else source
        description["write"] = "output" if name in self.output_name_to_type else "temporary" if name in self.step_name_to_value else "no rebinding"
        if self.surface != "original":
            selected_source = description["origin"]
            description["reference"] = self.reference(selected_source, name)
        return description

    def inspect_state(self, name: str | None, environment: EnvironmentSource, cursor: str | None = None) -> dict[str, Any]:
        if cursor is not None:
            if name is not None or environment != "effective":
                raise ValueError("Continue with cursor alone; omit name and environment")
            if cursor not in self.cursor_to_page:
                raise ValueError("Unknown cursor; restart nh_inspect without a cursor")
            entries, start = self.cursor_to_page[cursor]
        else:
            values = self.source_values(environment)
            if name is None:
                entries = [
                    self.describe_value(key, value, environment)
                    for key, value in sorted(values.items())
                    if not key.startswith("__") and not (environment == "effective" and key in vars(builtins))
                ]
            else:
                if name not in values:
                    if name in self.unbound_names and environment in ("effective", "python"):
                        raise UnboundLocalError(f"Python local {name!r} is unbound")
                    raise NameError(f"No {environment} name {name!r}; inspect that environment's names")
                value = values[name]
                # Prototype member discovery is deliberately limited to instance fields.
                fields = vars(value) if hasattr(value, "__dict__") and not callable(value) else {}
                entries = [self.describe_value(name, value, environment)]
                for field_name, field_value in sorted(fields.items()):
                    if field_name.startswith("_"):
                        continue
                    member = self.describe_value(f"{name}.{field_name}", field_value, environment)
                    member["origin"] = self.origin(name) if environment == "effective" else environment
                    if self.surface != "original":
                        source = self.origin(name) if environment == "effective" else environment
                        member["reference"] = f"{self.reference(source, name)}.{field_name}"
                    entries.append(member)
            start = 0
        end = start + self.page_size
        response: dict[str, Any] = {"entries": entries[start:end], "omitted": max(0, len(entries) - end)}
        if self.surface != "original":
            continuation = None
            if end < len(entries):
                continuation = f"page-{len(self.cursor_to_page) + 1}"
                self.cursor_to_page[continuation] = (entries, end)
            response["next_cursor"] = continuation
        return response

    def preflight(self, target_path: str) -> list[str]:
        segments = target_path.split(".")
        if any(not segment.isascii() or not segment.isidentifier() or segment.startswith("__") for segment in segments):
            raise ValueError("target_path must be name(.field)*")
        name = segments[0]
        if name == self.reference_root:
            raise ValueError("The inspection reference root is reserved; use another temporary name")
        if (
            len(segments) == 1
            and name not in self.output_name_to_type
            and (name in self.python_name_to_value or name in vars(builtins) or name in self.unbound_names)
        ):
            raise ValueError(f"{name!r} is not a declared output; use a new temporary or mutate the object in place")
        if len(segments) > 1 and name not in self.effective_values():
            raise NameError(f"Unknown assignment root {name!r}")
        return segments

    @staticmethod
    def compile_expression(expression: str) -> CodeType:
        tree = ast.parse(expression, mode="eval")
        if any(isinstance(node, ast.NamedExpr) for node in ast.walk(tree)):
            raise ValueError("Use tracked assignment instead of :=")
        return compile(tree, "<model-tools-prototype>", "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    async def evaluate_compiled(self, expression: CodeType, source: EnvironmentSource) -> Any:
        namespace = self.namespace(source)
        value = eval(expression, namespace, namespace)
        return await value if inspect.isawaitable(value) else value

    async def evaluate(self, expression: str, source: EnvironmentSource = "effective") -> Any:
        return await self.evaluate_compiled(self.compile_expression(expression), source)

    async def operation(
        self,
        kind: str,
        *,
        expression: str = "",
        target_path: str | None = None,
        environment: EnvironmentSource = "effective",
        name: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {"kind": kind, "expression": expression, "target": target_path, "environment": environment}
        if kind == "inspect":
            record.update(name=name, cursor=cursor)
        self.operations.append(record)
        evaluation_started = False
        response: dict[str, Any]
        try:
            if kind == "inspect":
                response = self.inspect_state(name, environment, cursor)
            else:
                segments = self.preflight(target_path) if target_path is not None else None
                compiled_expression = self.compile_expression(expression)
                evaluation_started = True
                value = await self.evaluate_compiled(compiled_expression, environment)
                if segments:
                    root = segments[0]
                    if len(segments) == 1:
                        if root in self.output_name_to_type:
                            value = TypeAdapter(self.output_name_to_type[root]).validate_python(value)
                        self.step_name_to_value[root] = value
                    else:
                        instance = self.effective_values()[root]
                        for segment in segments[1:-1]:
                            instance = getattr(instance, segment)
                        setattr(instance, segments[-1], value)
                    self.revision += 1
                response = {"value": preview(value)}
                if target_path is not None:
                    response["target"] = target_path
                    if self.surface != "original":
                        response["retained_as"] = (
                            "output" if target_path in self.output_name_to_type else "attribute" if "." in target_path else "temporary"
                        )
            record["status"] = "success"
            if self.surface != "original":
                response = {"status": "success", **response}
        except ApprovalRejectedError:
            record["status"] = "rejected"
            raise
        except asyncio.CancelledError:
            record["status"] = "interrupted"
            raise
        except Exception as exception:
            record["status"] = "error"
            response = {"error": str(exception), "error_type": type(exception).__name__}
            if self.surface != "original":
                response.update(status="error", evaluation_started=evaluation_started)
                if evaluation_started:
                    response["guidance"] = "Evaluation may have had effects. Inspect retained state before repeating an effectful call."
                elif isinstance(exception, SyntaxError):
                    response["guidance"] = (
                        "Use a single Python expression. Store a value with the assignment tool/destination instead of a Python assignment statement."
                    )
        record["response"] = response
        return response

    def output_values(self) -> dict[str, Any]:
        values = self.effective_values()
        return {
            name: TypeAdapter(annotation).validate_python(values[name]) for name, annotation in self.output_name_to_type.items() if name in values
        }


def build_tools(workspace: Workspace, guidance: Literal["short", "examples"]) -> list[Tool[Any]]:
    async def evaluate_original(expression: str, environment: EnvironmentSource = "effective") -> dict[str, Any]:
        return await workspace.operation("evaluate", expression=expression, environment=environment)

    async def evaluate_refined(expression: str) -> dict[str, Any]:
        return await workspace.operation("evaluate", expression=expression)

    async def assign(target_path: str, expression: str) -> dict[str, Any]:
        return await workspace.operation("assign", expression=expression, target_path=target_path)

    async def evaluate_merged(expression: str, target_path: str | None = None) -> dict[str, Any]:
        return await workspace.operation("assign" if target_path else "evaluate", expression=expression, target_path=target_path)

    async def inspect_original(name: str | None = None, environment: EnvironmentSource = "effective") -> dict[str, Any]:
        return await workspace.operation("inspect", name=name, environment=environment)

    async def inspect_refined(name: str | None = None, environment: EnvironmentSource = "effective", cursor: str | None = None) -> dict[str, Any]:
        return await workspace.operation("inspect", name=name, environment=environment, cursor=cursor)

    evaluation_description = "Evaluate a Python expression: read a value, call a function, or mutate an object in place."
    assignment_description = "Compute and retain a value in a declared output or a new temporary name; dotted targets mutate attributes."
    inspection_description = "Discover missing names, fields, signatures, or shadowed values. Not required before every operation."
    if guidance == "examples":
        evaluation_description += " Examples: len(items), items.sort(), host.execute(action)."
        assignment_description += " Example: target_path='result', expression='search_documents(question)' calls once and stores directly."
        inspection_description += " Example: name='search_documents', environment='task' finds the injected callable."
    tools: list[Tool[Any]] = []
    if workspace.surface == "merged":
        tools.append(
            Tool(
                evaluate_merged,
                name="nh_eval",
                sequential=True,
                description=evaluation_description
                + " Supply target_path to retain the value; omit it for evaluation only. "
                + assignment_description,
            )
        )
    else:
        description = evaluation_description
        if workspace.surface == "original":
            description += " environment selects the namespace for the whole expression; the default is effective."
            evaluation_tool = Tool(evaluate_original, name="nh_eval", sequential=True, description=description)
        else:
            evaluation_tool = Tool(evaluate_refined, name="nh_eval", sequential=True, description=description)
        tools.extend(
            [
                evaluation_tool,
                Tool(assign, name="nh_assign", sequential=True, description=assignment_description),
            ]
        )
    if workspace.surface != "original":
        inspection_description += " Use returned reference expressions directly. Continue a partial listing using next_cursor as cursor alone."
    if workspace.surface == "original":
        tools.append(Tool(inspect_original, name="nh_inspect", sequential=True, description=inspection_description))
    else:
        tools.append(Tool(inspect_refined, name="nh_inspect", sequential=True, description=inspection_description))
    return tools


def system_prompt(workspace: Workspace) -> str:
    prompt = """Execute one Natural block using the available Python values and tools.
<name> refers to a readable value. <:name> declares an output written back to Python.
Temporary names are retained for this step and inherited by nested calls.
Python names win over parent state, which wins over task injection. Unbound Python locals do not fall back.
Only declared Python outputs can be rebound; existing lists and other objects can be mutated in place.
Tool results are bounded previews, not canonical state. Retain large or opaque values instead of reconstructing them from previews.
Expressions can call and await functions. Top-level awaitable results are awaited automatically; use await for async calls composed inside another expression.
A successful None result is not an error.
Perform dependent operations sequentially. Finish with pass after completing the work unless asked to return, raise, break, or continue.
Return expressions are evaluated once at completion. Do not repeat an effectful call merely to return its result.
"""
    if workspace.surface == "original":
        prompt += f"Explicit cross-environment references are available as {workspace.reference_root}[source][name], where source is python, parent, task, or step.\n"
    return prompt


def user_prompt(workspace: Workspace, instruction: str) -> str:
    values = workspace.effective_values()
    visible = [
        {
            "name": name,
            "type": type(values[name]).__name__,
            "preview": preview(values[name]),
            **(
                {
                    "signature": str(inspect.signature(values[name])),
                    "description": inspect.getdoc(values[name]) or "",
                    "async": inspect.iscoroutinefunction(values[name]),
                }
                if callable(values[name])
                else {}
            ),
        }
        for name in workspace.python_name_to_value
        if name in values
    ]
    # Initial presentation is identical across surfaces: namespace selection is
    # tested through tool schemas/responses, not a favorable prompt renderer.
    injected = list(workspace.task_name_to_value)[: workspace.page_size]
    parent = {name: preview(value) for name, value in workspace.parent_name_to_value.items()}
    return json.dumps(
        {
            "program": instruction,
            "python": visible,
            "parent": parent,
            "task_names": injected,
            "task_omitted": max(0, len(workspace.task_name_to_value) - len(injected)),
            "outputs": {name: str(annotation) for name, annotation in workspace.output_name_to_type.items()},
            "unbound_python_names": sorted(workspace.unbound_names),
        },
        ensure_ascii=True,
    )
