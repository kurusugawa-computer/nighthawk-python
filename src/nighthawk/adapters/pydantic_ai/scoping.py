from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import Tool

from nighthawk.adapters.pydantic_ai.context import ContextBuilder, DefaultContextBuilder
from nighthawk.adapters.pydantic_ai.tools.declarations import ToolDeclaration, ToolEntry, resolve_scoped_tools
from nighthawk.core.configuration import UNSET, Extend, UnsetType
from nighthawk.core.scoping import get_scope, optional_scope
from nighthawk.core.step_context import StepContext


@dataclass(frozen=True)
class ModelScope:
    _run_identity: object = field(default=None, repr=False)
    context_builder: ContextBuilder = field(default_factory=DefaultContextBuilder)
    system_prompt_suffix_fragments: tuple[str, ...] = ()
    user_prompt_suffix_fragments: tuple[str, ...] = ()
    capabilities: tuple[AbstractCapability[StepContext], ...] = ()
    _tool_declarations: tuple[ToolDeclaration, ...] = field(default=(), repr=False)

    @property
    def tools(self) -> tuple[Tool[StepContext], ...]:
        return tuple(declaration.tool for declaration in self._tool_declarations)


_model_scope: ContextVar[ModelScope | None] = ContextVar("nighthawk_pydantic_ai_scope", default=None)


def get_model_scope() -> ModelScope:
    current = _model_scope.get()
    execution = optional_scope()
    identity = execution._run_identity if execution is not None else None
    return current if current is not None and current._run_identity is identity else ModelScope(_run_identity=identity)


def _compose_sequence[T](current: tuple[T, ...], supplied: Sequence[T] | Extend[T] | UnsetType) -> tuple[T, ...]:
    if isinstance(supplied, UnsetType):
        return current
    if isinstance(supplied, Extend):
        return (*current, *supplied.values)
    if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes, bytearray)):
        raise TypeError("Scope collections require a sequence, Extend, or UNSET")
    return tuple(supplied)


@contextmanager
def model_scope(
    *,
    context_builder: ContextBuilder | UnsetType = UNSET,
    system_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    user_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    tools: Sequence[ToolEntry] | Extend[ToolEntry] | UnsetType = UNSET,
    capabilities: Sequence[AbstractCapability[StepContext]] | Extend[AbstractCapability[StepContext]] | UnsetType = UNSET,
) -> Generator[ModelScope]:
    """Select model presentation and tool exposure within the active Core run."""
    get_scope()
    current = get_model_scope()
    if not isinstance(context_builder, (UnsetType, ContextBuilder)):
        raise TypeError("context_builder must implement ContextBuilder or be UNSET")
    system_fragments = _compose_sequence(current.system_prompt_suffix_fragments, system_prompt_suffix_fragments)
    user_fragments = _compose_sequence(current.user_prompt_suffix_fragments, user_prompt_suffix_fragments)
    if any(not isinstance(fragment, str) for fragment in (*system_fragments, *user_fragments)):
        raise TypeError("Prompt fragments must be strings")
    selected_capabilities = _compose_sequence(current.capabilities, capabilities)
    if any(not isinstance(capability, AbstractCapability) for capability in selected_capabilities):
        raise TypeError("Capabilities must be AbstractCapability instances")
    declarations = current._tool_declarations
    if isinstance(tools, Extend):
        declarations = resolve_scoped_tools(declarations, tools.values)
    elif not isinstance(tools, UnsetType):
        declarations = resolve_scoped_tools((), _compose_sequence((), tools))
    selected = replace(
        current,
        context_builder=current.context_builder if isinstance(context_builder, UnsetType) else context_builder,
        system_prompt_suffix_fragments=system_fragments,
        user_prompt_suffix_fragments=user_fragments,
        capabilities=selected_capabilities,
        _tool_declarations=declarations,
    )
    token = _model_scope.set(selected)
    try:
        yield selected
    finally:
        _model_scope.reset(token)


@contextmanager
def system_prompt_suffix_fragment_scope(fragment: str) -> Generator[None]:
    with model_scope(system_prompt_suffix_fragments=Extend([fragment])):
        yield
