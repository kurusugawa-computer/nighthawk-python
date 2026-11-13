from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.tools import Tool

from nighthawk.adapters.pydantic_ai.errors import ToolDeclarationError, ToolNameConflictError
from nighthawk.adapters.pydantic_ai.tools.provided import (
    STRUCTURED_ASSIGNMENT_TOOL_NAME_PREFIX,
    get_provided_tools,
    get_structured_assignment_tools,
)
from nighthawk.core.step_context import StepContext, optional_step_context

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
        if tool.name.startswith(STRUCTURED_ASSIGNMENT_TOOL_NAME_PREFIX):
            raise ToolNameConflictError(
                f"Tool name {tool.name!r} uses the prefix {STRUCTURED_ASSIGNMENT_TOOL_NAME_PREFIX!r}, reserved for the built-in tools of declared outputs"
            )
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
    """Built-in tools, the structured assignment tools of the innermost active step, and the scoped tools."""
    from nighthawk.adapters.pydantic_ai.scoping import get_model_scope

    step_context = optional_step_context()
    structured_assignment_tools = () if step_context is None else get_structured_assignment_tools(step_context)
    return [*get_provided_tools(), *structured_assignment_tools, *get_model_scope().tools]
