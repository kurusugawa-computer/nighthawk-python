from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

EXECUTION_EFFECT_TYPES: tuple[str, ...] = ("return", "break", "continue")

type ExecutionEffectType = Literal["return", "break", "continue"]


class ExecutionEffect(BaseModel, extra="forbid"):
    type: ExecutionEffectType
    source_path: str | None = None


class ExecutionErrorDetail(BaseModel, extra="forbid"):
    message: str
    type: str | None = None


class ExecutionFinal(BaseModel, extra="forbid"):
    effect: ExecutionEffect | None = None
    error: ExecutionErrorDetail | None = None
