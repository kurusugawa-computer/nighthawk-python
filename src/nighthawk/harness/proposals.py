from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nighthawk.harness._call_contracts import build_value_adapter
from nighthawk.harness.grants import GrantEntryKind, _validate_identifier
from nighthawk.harness.perimeter import EffectFootprint

type ExecutionIntent = Literal["definition_only", "definition_and_invocation"]


class DependencyDeclaration(BaseModel):
    """One nonlocal dependency of generated source, resolved from the grant by name and declared type.

    ``declared_type`` is the type the generated source was written against. For
    a ``capability`` dependency it is the capability's declared call signature
    text exactly as projected into the prompt (for example ``"(name: str) -> str"``);
    the host rejects the dispatch when the granted capability's signature
    differs. For a ``reference`` dependency it is a Python type expression the
    granted reference must satisfy under strict validation (for example ``str``
    or ``list[int]``); the object itself is bound, never a coerced copy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    kind: GrantEntryKind
    declared_type: object

    @field_validator("name")
    @classmethod
    def _validate_name(cls, name: str) -> str:
        _validate_identifier(name, role="DependencyDeclaration.name")
        return name

    @model_validator(mode="after")
    def _validate_declared_type(self) -> DependencyDeclaration:
        if self.kind == "capability":
            if not isinstance(self.declared_type, str) or not self.declared_type.strip():
                raise ValueError("a capability dependency declares its type as the capability's signature text")
            return self
        if isinstance(self.declared_type, str):
            raise ValueError("a reference dependency declares its type as a Python type expression, not text")  # noqa: TRY004 - Pydantic validators report invalid declarations as validation errors.
        try:
            build_value_adapter(self.declared_type)
        except Exception as error:
            raise ValueError("a reference dependency must declare a validatable Python type") from error
        return self


class CodeProposal(BaseModel):
    """Inert typed proposal of Python source for host-owned dispatch.

    ``execution_intent`` is exactly one of ``definition_only`` (materialize the
    declared exports without invoking them) or ``definition_and_invocation``
    (materialize, then invoke ``entry_export_name`` with the entry arguments
    under its call contract).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    source: str = Field(min_length=1)
    execution_intent: ExecutionIntent = "definition_only"
    export_name_tuple: tuple[str, ...] = Field(min_length=1)
    dependency_tuple: tuple[DependencyDeclaration, ...] = ()
    effect_footprint: EffectFootprint = EffectFootprint()
    entry_export_name: str | None = None
    entry_argument_tuple: tuple[object, ...] = ()
    entry_keyword_argument_name_to_value: dict[str, object] = Field(default_factory=dict)

    @field_validator("export_name_tuple")
    @classmethod
    def _validate_export_names(cls, export_name_tuple: tuple[str, ...]) -> tuple[str, ...]:
        for export_name in export_name_tuple:
            _validate_identifier(export_name, role="CodeProposal export name")
            if export_name.startswith("_"):
                raise ValueError(f"export name {export_name!r} must not start with an underscore")
        if len(set(export_name_tuple)) != len(export_name_tuple):
            raise ValueError("export names must be unique")
        return export_name_tuple

    @field_validator("dependency_tuple")
    @classmethod
    def _validate_dependencies(cls, dependency_tuple: tuple[DependencyDeclaration, ...]) -> tuple[DependencyDeclaration, ...]:
        name_list = [dependency.name for dependency in dependency_tuple]
        if len(set(name_list)) != len(name_list):
            raise ValueError("dependency names must be unique")
        return dependency_tuple

    @model_validator(mode="after")
    def _validate_intent(self) -> CodeProposal:
        if self.execution_intent == "definition_only":
            if self.entry_export_name is not None or self.entry_argument_tuple or self.entry_keyword_argument_name_to_value:
                raise ValueError("definition_only proposals carry no entry export or entry arguments")
            return self
        if self.entry_export_name is None:
            raise ValueError("definition_and_invocation proposals must name entry_export_name")
        if self.entry_export_name not in self.export_name_tuple:
            raise ValueError("entry_export_name must be one of the declared exports")
        return self

    @property
    def dependency_name_tuple(self) -> tuple[str, ...]:
        return tuple(dependency.name for dependency in self.dependency_tuple)

    @property
    def defining_source_identity(self) -> str:
        """Content identity of the source text (SHA-256, hexadecimal)."""

        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()
