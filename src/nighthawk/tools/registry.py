from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal

from pydantic_ai.tools import Tool

from ..errors import ToolRegistrationError
from ..runtime.step_context import StepContext
from .provided import build_provided_tool_definitions

type ToolEntry = Callable[..., Any] | Tool[StepContext]
"""A tool declaration accepted by ``nighthawk.scope(tools=...)``.

A plain callable is wrapped with ``pydantic_ai.tools.Tool``: the tool name is the function ``__name__``,
the description is the docstring, and a leading ``RunContext[StepContext]`` parameter is detected automatically.
Pass a ``Tool`` instance directly to override the name, description, or metadata.
"""

_builtin_tool_name_to_tool: dict[str, Tool[StepContext]] = {}
_builtin_tools_registered = False

scoped_tools_var: ContextVar[tuple[Tool[StepContext], ...]] = ContextVar(
    "nighthawk_scoped_tools",
    default=(),
)

_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_tool_name(name: str) -> None:
    try:
        name.encode("ascii")
    except UnicodeEncodeError as e:
        raise ToolRegistrationError(f"Tool name must be ASCII: {name!r}") from e

    if not _VALID_NAME_PATTERN.fullmatch(name):
        raise ToolRegistrationError(f"Tool name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")


def ensure_builtin_tools_registered() -> None:
    global _builtin_tools_registered

    if _builtin_tools_registered:
        return

    for builtin_definition in build_provided_tool_definitions():
        _validate_tool_name(builtin_definition.name)
        if builtin_definition.name in _builtin_tool_name_to_tool:
            raise ToolRegistrationError(f"Duplicate builtin tool name: {builtin_definition.name!r}")
        _builtin_tool_name_to_tool[builtin_definition.name] = builtin_definition.tool

    _builtin_tools_registered = True


def _normalize_tool_entry(entry: ToolEntry) -> Tool[StepContext]:
    if isinstance(entry, Tool):
        return entry
    if callable(entry):
        return Tool(entry)
    raise ToolRegistrationError(f"Tool entry must be a callable or pydantic_ai.tools.Tool: {entry!r}")


def resolve_scoped_tools(
    current: tuple[Tool[StepContext], ...],
    entries: Sequence[ToolEntry],
    *,
    mode: Literal["inherit", "replace"],
) -> tuple[Tool[StepContext], ...]:
    """Compute the scoped tool tuple for ``nighthawk.scope(tools=entries, mode=mode)``.

    Raises ``ToolRegistrationError`` when a name is invalid, collides with a built-in tool, or appears twice.
    """
    ensure_builtin_tools_registered()

    resolved: list[Tool[StepContext]] = list(current) if mode == "inherit" else []
    seen_name_set = {tool.name for tool in resolved}

    for entry in entries:
        tool = _normalize_tool_entry(entry)
        _validate_tool_name(tool.name)
        if tool.name in _builtin_tool_name_to_tool:
            raise ToolRegistrationError(f"Tool name conflicts with a built-in tool: {tool.name!r}")
        if tool.name in seen_name_set:
            raise ToolRegistrationError(f"Tool name conflict: {tool.name!r}")
        seen_name_set.add(tool.name)
        resolved.append(tool)

    return tuple(resolved)


def get_scoped_tools() -> tuple[Tool[StepContext], ...]:
    return scoped_tools_var.get()


def get_visible_tools() -> list[Tool[StepContext]]:
    """Return built-in tools followed by the tools declared for the current scope."""
    ensure_builtin_tools_registered()
    return [*_builtin_tool_name_to_tool.values(), *scoped_tools_var.get()]
