from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .context import ExecutionContext


class ExecutionEffect(BaseModel, extra="forbid"):
    type: Literal["continue", "break", "return"]
    value_json: str | None = None


class ExecutionErrorDetail(BaseModel, extra="forbid"):
    message: str
    type: str | None = None


class ExecutionFinal(BaseModel, extra="forbid"):
    effect: ExecutionEffect | None = None
    error: ExecutionErrorDetail | None = None


type ExecutionAgent = Agent[ExecutionContext, ExecutionFinal]
