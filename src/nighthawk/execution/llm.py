from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .context import ExecutionContext
from .environment import ExecutionEnvironment


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


def make_agent(environment: ExecutionEnvironment) -> ExecutionAgent:
    agent: ExecutionAgent = Agent(
        model=environment.execution_configuration.model,
        output_type=ExecutionFinal,
        deps_type=ExecutionContext,
        system_prompt=(environment.execution_configuration.prompts.execution_system_prompt_template),
    )
    return agent
