"""Frozen executor contracts containing live application values."""

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, InstanceOf, field_validator, model_validator

from ..composition import UNSET, UnsetType
from .environment import StepEnvironment, UnboundEnvironmentNameError
from .execution_reference import ExecutionReference
from .step_context import StepContext
from .step_contract import StepKind
from .step_result import Raise, StepResult


def _snapshot_output_bindings(environment: StepEnvironment, output_binding_names: Sequence[str]) -> dict[str, object]:
    binding_name_to_value: dict[str, object] = {}
    for name in output_binding_names:
        if name in environment.step_name_to_value:
            binding_name_to_value[name] = environment.step_name_to_value[name]
            continue
        try:
            binding_name_to_value[name] = environment.python_name_to_value[name]
        except (KeyError, UnboundEnvironmentNameError):
            continue
    return binding_name_to_value


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

    def snapshot_output_bindings(self) -> dict[str, object]:
        """Snapshot initialized declared outputs in declaration order.

        Tracked assignments precede current Python values, including live globals and cells.
        Unbound names and task-only references are excluded.
        The dictionary is independent; values retain identity.
        This neither validates nor writes values.
        Later working changes do not change its name associations.
        """
        return _snapshot_output_bindings(self.step_context.environment, self.output_binding_names)

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
    """Resolved control flow and output selection, pending runtime validation.

    Omission or UNSET selects initialized declared outputs at finalization.
    A mapping supplies the entire output selection; {} selects no writes.
    Explicit selections receive no supplementation from the working environment.
    Raise permits only omitted or empty outputs.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid", validate_default=True)

    outcome: StepResult
    binding_name_to_value: Mapping[str, object] | UnsetType = UNSET

    @field_validator("binding_name_to_value", mode="after")
    @classmethod
    def preserve_live_values(cls, value: Mapping[str, object] | UnsetType) -> Mapping[str, object] | UnsetType:
        return value if isinstance(value, UnsetType) else MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_output_selection(self) -> "StepCandidate":
        if isinstance(self.outcome, Raise) and not isinstance(self.binding_name_to_value, UnsetType) and len(self.binding_name_to_value):
            raise ValueError("StepCandidate Raise cannot include output bindings")
        return self
