from dataclasses import dataclass, field

from nighthawk.core.configuration import UNSET, UnsetType


@dataclass(frozen=True)
class ModelSelection:
    """Inert selections interpreted and validated by the active executor.

    UNSET inherits; None clears a model. Configuration replacement is exclusive with individual selections.
    Models and configuration retain identity and are opaque to Core.
    """

    execution_model: object = field(default=UNSET, repr=False)
    decision_model: object = field(default=UNSET, repr=False)
    configuration: object = field(default=UNSET, repr=False)

    def __post_init__(self) -> None:
        selected = any(not isinstance(value, UnsetType) for value in (self.execution_model, self.decision_model))
        if selected == (not isinstance(self.configuration, UnsetType)):
            raise ValueError("Supply model selections or configuration, exclusively")


def model_selection(execution_model: object, decision_model: object, configuration: object) -> ModelSelection | None:
    if all(isinstance(value, UnsetType) for value in (execution_model, decision_model, configuration)):
        return None
    return ModelSelection(execution_model, decision_model, configuration)
