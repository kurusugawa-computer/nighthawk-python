from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from .agent import ToolContext
from .errors import ToolRegistrationError


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    tool: Tool[ToolContext]


_builtin_tool_definitions: dict[str, ToolDefinition] = {}
_builtin_tools_registered = False

_global_tool_definitions: dict[str, ToolDefinition] = {}

_environment_scope_stack_var: ContextVar[list[dict[str, ToolDefinition]]] = ContextVar(
    "nighthawk_environment_tool_scope_stack",
    default=[],
)

_call_scope_stack_var: ContextVar[list[dict[str, ToolDefinition]]] = ContextVar(
    "nighthawk_call_tool_scope_stack",
    default=[],
)

_VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_tool_name(name: str) -> None:
    try:
        name.encode("ascii")
    except UnicodeEncodeError as e:
        raise ToolRegistrationError(f"Tool name must be ASCII: {name!r}") from e

    if not _VALID_NAME_RE.fullmatch(name):
        raise ToolRegistrationError(f"Tool name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")


def ensure_builtin_tools_registered() -> None:
    global _builtin_tools_registered

    if _builtin_tools_registered:
        return

    from .builtin_tools import get_builtin_tool_definitions

    builtin_definitions: dict[str, ToolDefinition] = {}
    for tool_definition in get_builtin_tool_definitions():
        _validate_tool_name(tool_definition.name)
        if tool_definition.name in builtin_definitions:
            raise ToolRegistrationError(f"Duplicate builtin tool name: {tool_definition.name!r}")
        builtin_definitions[tool_definition.name] = tool_definition

    _builtin_tool_definitions.update(builtin_definitions)
    _builtin_tools_registered = True


def _visible_tool_definitions() -> dict[str, ToolDefinition]:
    ensure_builtin_tools_registered()

    merged: dict[str, ToolDefinition] = dict(_builtin_tool_definitions)
    merged.update(_global_tool_definitions)

    for scope in _environment_scope_stack_var.get():
        merged.update(scope)

    for scope in _call_scope_stack_var.get():
        merged.update(scope)

    return merged


def _register_tool_definition(tool_definition: ToolDefinition, *, overwrite: bool) -> None:
    ensure_builtin_tools_registered()

    name = tool_definition.name
    visible = _visible_tool_definitions()

    if name in visible and not overwrite:
        raise ToolRegistrationError(f"Tool name conflict: {name!r}. Pass overwrite=True to replace the visible definition.")

    call_scope_stack = _call_scope_stack_var.get()
    if call_scope_stack:
        current_scope = dict(call_scope_stack[-1])
        current_scope[name] = tool_definition
        next_stack = [*call_scope_stack[:-1], current_scope]
        _call_scope_stack_var.set(next_stack)
        return

    environment_scope_stack = _environment_scope_stack_var.get()
    if environment_scope_stack:
        current_scope = dict(environment_scope_stack[-1])
        current_scope[name] = tool_definition
        next_stack = [*environment_scope_stack[:-1], current_scope]
        _environment_scope_stack_var.set(next_stack)
        return

    _global_tool_definitions[name] = tool_definition


@contextmanager
def environment_scope() -> Iterator[None]:
    current = _environment_scope_stack_var.get()
    token = _environment_scope_stack_var.set([*current, {}])
    try:
        yield
    finally:
        _environment_scope_stack_var.reset(token)


@contextmanager
def call_scope() -> Iterator[None]:
    current = _call_scope_stack_var.get()
    token = _call_scope_stack_var.set([*current, {}])
    try:
        yield
    finally:
        _call_scope_stack_var.reset(token)


def get_visible_tools() -> list[Tool[ToolContext]]:
    ensure_builtin_tools_registered()

    # Snapshot to avoid mutation during toolset construction.
    visible = dict(_visible_tool_definitions())
    return [definition.tool for definition in visible.values()]


ToolFunction = Callable[..., Any]


def tool(
    func: ToolFunction | None = None,
    /,
    *,
    name: str | None = None,
    overwrite: bool = False,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    def decorator(inner: ToolFunction) -> ToolFunction:
        ensure_builtin_tools_registered()

        tool_name = name or inner.__name__
        _validate_tool_name(tool_name)

        resolved_description = description
        if resolved_description is None:
            resolved_description = inner.__doc__

        tool_object: Tool[ToolContext] = Tool(
            inner,
            name=tool_name,
            description=resolved_description,
            metadata=metadata,
        )

        tool_definition = ToolDefinition(name=tool_name, tool=tool_object)
        _register_tool_definition(tool_definition, overwrite=overwrite)
        return inner

    if func is not None:
        return decorator(func)

    return decorator


def reset_global_tools_for_tests() -> None:
    _global_tool_definitions.clear()


def require_tool_signature(_run_context: RunContext[ToolContext], /) -> None:
    # This exists to anchor documentation and to provide a stable import location for
    # the required RunContext signature. It is not called.
    _ = _run_context
    raise NotImplementedError
