"""Frozen executor contracts containing live application values."""

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, InstanceOf, field_validator, model_validator

from .execution_reference import ExecutionReference
from .step_context import StepContext
from .step_contract import StepKind
from .step_result import StepResult


class StepRequest(BaseModel):
    """One execution boundary and its selected contracts.

    The context owns the environment. Contract mappings are shallow snapshots;
    neither request construction nor validation copies application values.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid", validate_default=True)

    execution_reference: ExecutionReference
    processed_natural_program: str
    step_context: InstanceOf[StepContext]
    output_binding_names: tuple[str, ...]
    binding_name_to_type: Mapping[str, object]
    return_annotation: object
    allowed_step_kinds: tuple[StepKind, ...]
    allow_awaitable_return: bool = False

    @field_validator("binding_name_to_type", mode="after")
    @classmethod
    def freeze_contracts(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_boundary(self) -> "StepRequest":
        if self.execution_reference != self.step_context.execution_reference:
            raise ValueError("StepRequest execution reference must match its context")
        if len(set(self.output_binding_names)) != len(self.output_binding_names):
            raise ValueError("StepRequest output names must be unique")
        if set(self.binding_name_to_type) != set(self.output_binding_names):
            raise ValueError("StepRequest requires one contract for each declared output")
        if not self.allowed_step_kinds:
            raise ValueError("StepRequest requires at least one allowed outcome kind")
        return self


class StepCandidate(BaseModel):
    """Resolved control flow and proposed writes, pending runtime finalization."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid", validate_default=True)

    outcome: StepResult
    binding_name_to_value: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("binding_name_to_value", mode="after")
    @classmethod
    def preserve_live_values(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))
