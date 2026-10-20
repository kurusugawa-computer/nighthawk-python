"""Reusable declarations for trusted embedded Natural steps."""

from collections.abc import Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
from types import MappingProxyType
from typing import Literal

from .bindings import normalize_binding_names, require_declared_binding_names

type BlockKind = Literal["execute", "decide"]


@dataclass(frozen=True)
class StepDefinition:
    """Immutable program metadata, independent of any invocation or namespace.

    The program is trusted source without the ``execute`` sentinel.
    Binding declarations are authoritative; source interpolation and include expansion are not performed.
    Declaration order controls validation and saving.
    Mappings and sequences are copied, while annotation objects retain identity.
    Missing annotations are inferred from each invocation's initial values.
    Forward annotations resolve in that invocation's explicit environment.
    ``source_location`` identifies this program in lifecycle records.
    """

    natural_program: str
    _: KW_ONLY
    block_kind: BlockKind = "execute"
    input_binding_names: Sequence[str] = ()
    output_binding_names: Sequence[str] = ()
    binding_name_to_type: Mapping[str, object] = field(default_factory=dict)
    return_annotation: object = object
    is_in_loop: bool = False
    source_location: str = "<embedded>:0"

    def __post_init__(self) -> None:
        if not isinstance(self.natural_program, str):
            raise TypeError("natural_program must be a string")
        if self.block_kind not in ("execute", "decide"):
            raise ValueError("block_kind must be execute or decide")
        inputs = normalize_binding_names(self.input_binding_names)
        outputs = normalize_binding_names(self.output_binding_names)
        if self.block_kind == "decide" and len(outputs) != 1:
            raise ValueError("decide requires exactly one output binding")
        declared_name_to_type = dict(self.binding_name_to_type)
        require_declared_binding_names(declared_name_to_type, outputs)
        object.__setattr__(self, "input_binding_names", inputs)
        object.__setattr__(self, "output_binding_names", outputs)
        object.__setattr__(self, "binding_name_to_type", MappingProxyType(declared_name_to_type))
