from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..configuration import RunConfiguration

if TYPE_CHECKING:
    from .step_executor import StepExecutor


@dataclass(frozen=True)
class Environment:
    run_configuration: RunConfiguration
    step_executor: "StepExecutor"

    run_id: str = ""
    scope_id: str = ""

    system_prompt_suffix_fragments: tuple[str, ...] = ()
    user_prompt_suffix_fragments: tuple[str, ...] = ()


__all__ = [
    "Environment",
]
