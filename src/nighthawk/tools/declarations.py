from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic_ai.tools import Tool

from ..errors import ToolDeclarationError, ToolNameConflictError
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


@dataclass(frozen=True)
class _ToolDeclaration:
    tool: Tool[StepContext]
    origin: ToolEntry


scoped_tools_var: ContextVar[tuple[_ToolDeclaration, ...]] = ContextVar(
    "nighthawk_scoped_tools",
    default=(),
)

_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_tool_name(name: str) -> None:
    if not isinstance(name, str):
        raise ToolDeclarationError("Tool name must be a string")
    try:
        name.encode("ascii")
    except UnicodeEncodeError as e:
        raise ToolDeclarationError(f"Tool name must be ASCII: {name!r}") from e

    if not _VALID_NAME_PATTERN.fullmatch(name):
        raise ToolDeclarationError(f"Tool name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")


def ensure_builtin_tools_registered() -> None:
    global _builtin_tools_registered

    if _builtin_tools_registered:
        return

    for builtin_definition in build_provided_tool_definitions():
        _validate_tool_name(builtin_definition.name)
        if builtin_definition.name in _builtin_tool_name_to_tool:
            raise ToolNameConflictError(f"Duplicate builtin tool name: {builtin_definition.name!r}")
        _builtin_tool_name_to_tool[builtin_definition.name] = builtin_definition.tool

    _builtin_tools_registered = True


def _normalize_tool_entry(entry: ToolEntry) -> Tool[StepContext]:
    if isinstance(entry, Tool):
        return entry
    if callable(entry):
        try:
            return Tool(entry)
        except Exception as exception:
            raise ToolDeclarationError("Cannot construct a tool declaration from this callable") from exception
    raise ToolDeclarationError("Tool entry must be a callable or pydantic_ai.tools.Tool")


def resolve_scoped_tools(
    current: tuple[_ToolDeclaration, ...],
    entries: Sequence[ToolEntry],
) -> tuple[_ToolDeclaration, ...]:
    """Append novel declarations, retaining the first normalized tool and order."""
    ensure_builtin_tools_registered()
    resolved = list(current)
    name_to_declaration = {declaration.tool.name: declaration for declaration in resolved}
    for entry in entries:
        tool = _normalize_tool_entry(entry)
        _validate_tool_name(tool.name)
        if tool.name in _builtin_tool_name_to_tool:
            raise ToolNameConflictError(f"Tool name conflicts with a built-in tool: {tool.name!r}")
        previous = name_to_declaration.get(tool.name)
        if previous is not None:
            if entry is previous.origin or entry is previous.tool:
                continue
            raise ToolNameConflictError(f"Tool name conflict: {tool.name!r}")
        declaration = _ToolDeclaration(tool, entry)
        name_to_declaration[tool.name] = declaration
        resolved.append(declaration)
    return tuple(resolved)


def get_scoped_tools() -> tuple[Tool[StepContext], ...]:
    return tuple(declaration.tool for declaration in scoped_tools_var.get())


def get_visible_tools() -> list[Tool[StepContext]]:
    """Return built-in tools followed by the tools declared for the current scope."""
    ensure_builtin_tools_registered()
    return [*_builtin_tool_name_to_tool.values(), *get_scoped_tools()]
