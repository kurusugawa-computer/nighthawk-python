from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.tools import Tool

from ..errors import ToolDeclarationError, ToolNameConflictError
from ..runtime.step_context import StepContext
from .provided import get_provided_tools

type ToolEntry = Callable[..., Any] | Tool[StepContext]
"""A tool declaration accepted by ``nighthawk.scope(tools=...)``.

A plain callable is wrapped with ``pydantic_ai.tools.Tool``: the tool name is the function ``__name__``,
the description is the docstring, and a leading ``RunContext[StepContext]`` parameter is detected automatically.
Pass a ``Tool`` instance directly to override the name, description, or metadata.
"""


@dataclass(frozen=True)
class ToolDeclaration:
    tool: Tool[StepContext]
    origin: ToolEntry


_VALID_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_tool_name(name: str) -> None:
    if not isinstance(name, str):
        raise ToolDeclarationError("Tool name must be a string")
    if not _VALID_NAME_PATTERN.fullmatch(name):
        raise ToolDeclarationError(f"Tool name must match ^[A-Za-z_][A-Za-z0-9_]*$: {name!r}")


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
    current: tuple[ToolDeclaration, ...],
    entries: Sequence[ToolEntry],
) -> tuple[ToolDeclaration, ...]:
    """Append novel declarations, retaining the first normalized tool and order."""
    builtin_tool_names = {tool.name for tool in get_provided_tools()}
    resolved = list(current)
    name_to_declaration = {declaration.tool.name: declaration for declaration in resolved}
    for entry in entries:
        tool = _normalize_tool_entry(entry)
        _validate_tool_name(tool.name)
        if tool.name in builtin_tool_names:
            raise ToolNameConflictError(f"Tool name conflicts with a built-in tool: {tool.name!r}")
        previous = name_to_declaration.get(tool.name)
        if previous is not None:
            if entry is previous.origin or entry is previous.tool:
                continue
            raise ToolNameConflictError(f"Tool name conflict: {tool.name!r}")
        declaration = ToolDeclaration(tool, entry)
        name_to_declaration[tool.name] = declaration
        resolved.append(declaration)
    return tuple(resolved)


def get_visible_tools() -> list[Tool[StepContext]]:
    from ..runtime.scoping import optional_scope

    active_scope = optional_scope()
    return [*get_provided_tools(), *(active_scope.tools if active_scope is not None else ())]
