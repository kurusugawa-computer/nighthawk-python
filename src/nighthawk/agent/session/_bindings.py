"""Session binding declarations use the core grammar and complete write-set validation.

Only the submitted cell declares bindings.
Historical text and tool results remain data.
Assignment is a reversible interior effect; typed validation and run admission retain the existing host boundary and do not confine object mutation.
"""

from __future__ import annotations

import keyword
import typing
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import ModuleType

from nighthawk.natural.blocks import extract_bindings
from nighthawk.runtime.bindings import MISSING_ANNOTATION, infer_binding_types, validate_output_bindings

from .._grants import Grant
from ._records import ConversationResult, FunctionDeclaration, SourceCandidate
from .errors import SessionLookupError


@dataclass
class _SessionBindings:
    read_name_tuple: tuple[str, ...]
    write_name_tuple: tuple[str, ...]
    binding_name_to_type: dict[str, object]
    pending_name_to_value: dict[str, object] = field(default_factory=dict)
    declare_function: Callable[[FunctionDeclaration], object] | None = None

    @classmethod
    def prepare(cls, task: str, binding_name_to_value: dict[str, object]) -> _SessionBindings:
        reads, writes = extract_bindings(task)
        for name in (*reads, *writes):
            if keyword.iskeyword(name):
                raise ValueError(f"binding name is a Python keyword: {name}")
        # Resolve only selected annotations, with the operator's Python globals.
        module = ModuleType("_nighthawk_binding_annotations")
        module.__dict__.update(binding_name_to_value)
        binding_name_to_annotation = typing.cast(dict[str, object], binding_name_to_value.get("__annotations__", {}))
        module.__annotations__ = {name: binding_name_to_annotation[name] for name in writes if name in binding_name_to_annotation}
        declared_binding_name_to_type = typing.get_type_hints(module, include_extras=True)
        binding_name_to_type = {name: declared_binding_name_to_type.get(name, MISSING_ANNOTATION) for name in writes}
        binding_name_to_type = infer_binding_types(binding_name_to_type, binding_name_to_value)
        return cls(reads, writes, binding_name_to_type)

    def require_reads(self, grant: Grant) -> None:
        for name in self.read_name_tuple:
            if name not in grant.references and name not in grant.capabilities:
                raise SessionLookupError(f"read binding is unavailable: {name}")

    def finalize(self, result: ConversationResult, run_id: str, cell_id: str) -> None:
        """Validate composition writes before admission of the completed run result."""
        proposed_binding_name_to_value = dict(result.binding_name_to_value)
        for request in result.source_retention_tuple:
            proposed_binding_name_to_value[request.binding_name] = SourceCandidate(request.proposal, run_id, cell_id)
        validated_binding_name_to_value = validate_output_bindings(proposed_binding_name_to_value, self.binding_name_to_type, self.write_name_tuple)
        self.pending_name_to_value.update(validated_binding_name_to_value)


_current_bindings: ContextVar[_SessionBindings | None] = ContextVar("nighthawk_session_bindings", default=None)


@contextmanager
def _binding_scope(bindings: _SessionBindings) -> Generator[None]:
    token = _current_bindings.set(bindings)
    try:
        yield
    finally:
        _current_bindings.reset(token)
